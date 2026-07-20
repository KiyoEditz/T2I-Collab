"""
Build the local tag autofill index from a raw Danbooru tags.csv export.

This is a one-time (but re-runnable) data processing step — run it whenever
you refresh tags.csv / tag_aliases.csv. It never runs at request time; the
web app only reads the SQLite file this script produces.

Expects, in this same folder (frontend/data/):
    tags.csv          required. Columns used: name, post_count, category,
                       is_deprecated. (id, created_at, updated_at, words are
                       read but ignored — see note below.)
    tag_aliases.csv    optional. Columns used: antecedent_name, consequent_name.
                       If missing, the index is still built, just without
                       old-name -> current-name resolution.

Produces:
    tags.db — SQLite database with:
        tags(name, category, post_count, search_text)
            one row per kept tag (artist tags and deprecated tags excluded).
            search_text = name with '_' -> ' ', lowercased.
        aliases(antecedent, antecedent_search, consequent)
            old-name -> current-name map, for tags that were renamed.
        tags_fts / aliases_fts
            FTS5 virtual tables (trigram tokenizer) over search_text /
            antecedent_search, so /api/tags can do a fast "substring anywhere
            in the tag" search — matching how danbooru's own search box
            behaves (e.g. typing "ela" finds "hasumi_elan", not just tags
            that start with "ela"). If this Python's sqlite3 build lacks
            FTS5/trigram support, the script still finishes and the app
            automatically falls back to a plain (slower) substring scan.

Why not use the raw `words` column for matching:
    It's a tokenized list (e.g. name "fate/stay_night" -> words
    ["fate","stay","night"]), not a single string, and token-only matching
    can't find a query that spans two tokens (like "purple ey" inside
    "purple_eyes") or a substring inside a single token (like "ela" inside
    "elaina"). A real substring index on the full name covers both.

Usage:
    cd frontend/data
    python build_tag_index.py
"""

import csv
import re
import sqlite3
import sys
import time
from pathlib import Path

DATA_DIR = Path(__file__).parent
TAGS_CSV = DATA_DIR / "tags.csv"
ALIASES_CSV = DATA_DIR / "tag_aliases.csv"
DB_PATH = DATA_DIR / "tags.db"

ARTIST_CATEGORY = 1
BATCH_SIZE = 20_000

csv.field_size_limit(sys.maxsize)


def normalize(name: str) -> str:
    """canonical underscore form -> lowercase, space-separated, for search."""
    return re.sub(r"[_/]+", " ", name).strip().lower()


def load_alias_source(path: Path) -> dict:
    """antecedent_name -> consequent_name, straight from tag_aliases.csv."""
    if not path.exists():
        print(f"  (no {path.name} found — building without alias resolution)")
        return {}
    mapping = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not {"antecedent_name", "consequent_name"} <= set(reader.fieldnames or []):
            print(f"  {path.name} is missing antecedent_name/consequent_name columns — skipping aliases")
            return {}
        for row in reader:
            old, new = row.get("antecedent_name", "").strip(), row.get("consequent_name", "").strip()
            if old and new:
                mapping[old] = new
    print(f"  loaded {len(mapping):,} alias pairs from {path.name}")
    return mapping


def try_build_fts(con: sqlite3.Connection, table: str, source_table: str, source_col: str) -> bool:
    """Create an external-content FTS5 trigram index over source_table.source_col.
    Returns True on success, False (leaving no partial table behind) if this
    SQLite build doesn't support FTS5 / the trigram tokenizer."""
    try:
        con.execute(
            f"CREATE VIRTUAL TABLE {table} USING fts5("
            f"{source_col}, content='{source_table}', content_rowid='rowid', tokenize='trigram')"
        )
        con.execute(f"INSERT INTO {table}({table}) VALUES('rebuild')")
        return True
    except sqlite3.OperationalError as e:
        con.execute(f"DROP TABLE IF EXISTS {table}")
        print(f"  NOTE: couldn't build FTS5 trigram index for {table} ({e}). "
              f"Substring search will still work, just slower.")
        return False


def build():
    if not TAGS_CSV.exists():
        raise SystemExit(f"tags.csv not found at {TAGS_CSV}")

    if DB_PATH.exists():
        DB_PATH.unlink()

    print("Loading tag_aliases.csv...")
    alias_source = load_alias_source(ALIASES_CSV)

    print("Building tag index (single pass over tags.csv, this can take a minute)...")
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA synchronous = OFF")
    con.execute("PRAGMA journal_mode = MEMORY")
    cur = con.cursor()

    cur.execute(
        "CREATE TABLE tags (name TEXT PRIMARY KEY, category INTEGER, post_count INTEGER, search_text TEXT)"
    )
    cur.execute("CREATE INDEX idx_tags_search_text ON tags(search_text)")
    cur.execute("CREATE TABLE aliases (antecedent TEXT PRIMARY KEY, antecedent_search TEXT, consequent TEXT)")
    cur.execute("CREATE INDEX idx_aliases_search ON aliases(antecedent_search)")

    tag_info = {}          # canonical name -> (category, post_count)
    deprecated_rows = []    # (old_name, resolved_canonical_name)
    tags_buffer = []

    def flush():
        if tags_buffer:
            cur.executemany("INSERT OR REPLACE INTO tags VALUES (?,?,?,?)", tags_buffer)
            tags_buffer.clear()

    start = time.time()
    kept, dropped_artist, dropped_deprecated, dropped_dupe = 0, 0, 0, 0

    con.execute("BEGIN")
    with TAGS_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, 1):
            try:
                category = int(row["category"])
            except (ValueError, TypeError):
                continue
            if category == ARTIST_CATEGORY:
                dropped_artist += 1
                continue

            name = row["name"].strip()
            if not name:
                continue
            is_deprecated = row.get("is_deprecated", "False").strip().lower() == "true"
            try:
                post_count = int(row.get("post_count") or 0)
            except ValueError:
                post_count = 0

            if is_deprecated:
                canonical = alias_source.get(name)
                if canonical:
                    deprecated_rows.append((name, canonical))
                dropped_deprecated += 1
                continue

            # A handful of rows in real-world tags.csv dumps have stray control
            # or full-width whitespace characters baked into the name (e.g.
            # "bandaid\r\n", "kirin\u3000"), which collapse onto an existing
            # tag once stripped. Keep whichever variant has the higher
            # post_count (the real tag) instead of crashing on the PK conflict.
            existing = tag_info.get(name)
            if existing is not None:
                dropped_dupe += 1
                if existing[1] >= post_count:
                    continue

            tag_info[name] = (category, post_count)
            tags_buffer.append((name, category, post_count, normalize(name)))
            kept += 1

            if len(tags_buffer) >= BATCH_SIZE:
                flush()
            if i % 200_000 == 0:
                print(f"  ...{i:,} rows read, {kept:,} kept so far")

    flush()

    # Resolve deprecated tag names to their canonical tag's info, and persist
    # the alias map (only kept if the canonical tag itself survived filtering).
    alias_pairs = []
    for old_name, canonical in deprecated_rows:
        if canonical in tag_info:
            alias_pairs.append((old_name, normalize(old_name), canonical))
    if alias_pairs:
        cur.executemany("INSERT OR REPLACE INTO aliases VALUES (?,?,?)", alias_pairs)

    con.commit()

    print("Building substring search index (FTS5 trigram)...")
    fts_ok = try_build_fts(con, "tags_fts", "tags", "search_text")
    if alias_pairs:
        try_build_fts(con, "aliases_fts", "aliases", "antecedent_search")
    con.commit()
    con.close()

    elapsed = time.time() - start
    print("\nDone in {:.1f}s".format(elapsed))
    print(f"  kept:                 {kept:,} tags")
    print(f"  dropped (artist):     {dropped_artist:,}")
    print(f"  dropped (deprecated): {dropped_deprecated:,}")
    if dropped_dupe:
        print(f"  duplicate-name rows resolved: {dropped_dupe:,} (kept the higher post_count variant)")
    print(f"  aliases resolved:     {len(alias_pairs):,}")
    print(f"  substring search:     {'fast (FTS5 trigram)' if fts_ok else 'basic (no FTS5/trigram in this Python)'}")
    print(f"  -> {DB_PATH}")


if __name__ == "__main__":
    build()
