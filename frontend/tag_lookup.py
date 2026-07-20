"""
Local Danbooru tag lookup, backed by the SQLite index built by
data/build_tag_index.py. No network calls — everything here is a read
against tags.db.

Matches Danbooru's own search box behavior: the query can match anywhere in
the tag name (not just the start), e.g. "ela" finds "elaina", "hasumi_elan",
and "lugosi_ela" alike, ranked by post_count. Renamed tags resolve through
the alias table, so a hit is returned with `alias_of` set to the old name
the query actually matched (for a "old_name → current_name" display).
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "tags.db"

# category int -> label the frontend's CSS already knows how to color.
CATEGORY_LABELS = {0: "general", 3: "copyright", 4: "character", 5: "meta"}

DEFAULT_LIMIT = 20
MAX_LIMIT = 50

# Below this length, FTS5 trigram substring search degrades to a full scan
# anyway (a trigram needs 3 characters), so we use a cheap indexed prefix
# lookup instead — still fast, just prefix-only for very short queries.
MIN_LEN_FOR_SUBSTRING = 3


class TagIndexNotBuilt(Exception):
    pass


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise TagIndexNotBuilt(
            "tags.db not found. Run `python data/build_tag_index.py` once to build it "
            "from data/tags.csv."
        )
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def _table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)
    ).fetchone() is not None


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _prefix_range(prefix: str):
    return prefix, prefix + "\uffff"


def _search_tags(cur, query: str, limit: int):
    if len(query) >= MIN_LEN_FOR_SUBSTRING:
        if _table_exists(cur, "tags_fts"):
            # NOTE: deliberately no ESCAPE clause here — SQLite only takes the
            # fast trigram-indexed path for a plain `col LIKE ?`; adding
            # ESCAPE (even unused) makes it fall back to a full scan, ~40x
            # slower. '%'/'_' in the query are extremely unlikely for a tag
            # search box, and '_' as a wildcard happens to line up with the
            # spaces in search_text anyway, so this is a safe trade-off.
            pattern = f"%{query}%"
            return cur.execute(
                """SELECT t.name, t.category, t.post_count FROM tags_fts f
                   JOIN tags t ON t.rowid = f.rowid
                   WHERE f.search_text LIKE ?
                   ORDER BY t.post_count DESC LIMIT ?""",
                (pattern, limit),
            ).fetchall()
        pattern = f"%{_escape_like(query)}%"
        return cur.execute(
            """SELECT name, category, post_count FROM tags
               WHERE search_text LIKE ? ESCAPE '\\'
               ORDER BY post_count DESC LIMIT ?""",
            (pattern, limit),
        ).fetchall()

    lo, hi = _prefix_range(query)
    return cur.execute(
        """SELECT name, category, post_count FROM tags
           WHERE search_text >= ? AND search_text < ?
           ORDER BY post_count DESC LIMIT ?""",
        (lo, hi, limit),
    ).fetchall()


def _search_aliases(cur, query: str, limit: int):
    if not _table_exists(cur, "aliases"):
        return []

    if len(query) >= MIN_LEN_FOR_SUBSTRING:
        if _table_exists(cur, "aliases_fts"):
            pattern = f"%{query}%"  # see _search_tags for why no ESCAPE here
            return cur.execute(
                """SELECT a.antecedent, t.name, t.category, t.post_count
                   FROM aliases_fts f
                   JOIN aliases a ON a.rowid = f.rowid
                   JOIN tags t ON t.name = a.consequent
                   WHERE f.antecedent_search LIKE ?
                   ORDER BY t.post_count DESC LIMIT ?""",
                (pattern, limit),
            ).fetchall()
        pattern = f"%{_escape_like(query)}%"
        return cur.execute(
            """SELECT a.antecedent, t.name, t.category, t.post_count
               FROM aliases a JOIN tags t ON t.name = a.consequent
               WHERE a.antecedent_search LIKE ? ESCAPE '\\'
               ORDER BY t.post_count DESC LIMIT ?""",
            (pattern, limit),
        ).fetchall()

    lo, hi = _prefix_range(query)
    return cur.execute(
        """SELECT a.antecedent, t.name, t.category, t.post_count
           FROM aliases a JOIN tags t ON t.name = a.consequent
           WHERE a.antecedent_search >= ? AND a.antecedent_search < ?
           ORDER BY t.post_count DESC LIMIT ?""",
        (lo, hi, limit),
    ).fetchall()


def search_tags(query: str, limit: int = DEFAULT_LIMIT) -> list:
    query = " ".join((query or "").strip().lower().split())  # collapse whitespace
    if len(query) < 2:
        return []
    limit = max(1, min(limit, MAX_LIMIT))

    con = _connect()
    try:
        cur = con.cursor()
        tag_rows = _search_tags(cur, query, limit)
        alias_rows = _search_aliases(cur, query, limit)
    finally:
        con.close()

    best: dict = {}       # name -> (category, post_count)
    alias_of: dict = {}   # name -> antecedent (old name), if the best hit came via an alias

    for name, category, post_count in tag_rows:
        best[name] = (category, post_count)

    for antecedent, name, category, post_count in alias_rows:
        if name not in best or post_count >= best[name][1]:
            best[name] = (category, post_count)
            alias_of[name] = antecedent

    ranked = sorted(best.items(), key=lambda kv: kv[1][1], reverse=True)[:limit]

    results = []
    for name, (category, post_count) in ranked:
        item = {"name": name, "category": CATEGORY_LABELS.get(category, "general"), "post_count": post_count}
        if name in alias_of:
            item["alias_of"] = alias_of[name]
        results.append(item)
    return results
