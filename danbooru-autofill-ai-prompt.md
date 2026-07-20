# Task: Implement Danbooru Tag Autofill in Existing Project

## Context

I have an existing project with a search/prompt input field. I need you to add a **local tag autofill feature** powered by a Danbooru tag dataset I've already downloaded. Do not scaffold a new project — integrate into what already exists.

## Step 0 — Investigate before writing code

Before implementing anything:
1. Inspect the project structure (framework, language, existing search/input component, state management, any existing local storage/DB usage).
2. Locate the search input component that should trigger the autofill dropdown.
3. Report back a short plan (data layer choice, integration point) before making large changes, unless the approach below is unambiguous given the stack.

## Input data

I have a raw CSV exported from a Danbooru tag mirror, with these columns:

```
id, name, category, post_count, created_at, updated_at, is_deprecated, word
```

- `id` — internal numeric ID, not needed for lookups, safe to drop.
- `name` — canonical tag name, underscore-separated (e.g. `long_hair`, `hatsune_miku`). This is the value that should actually be inserted into the prompt/search field when a suggestion is picked.
- `category` — integer: `0=general`, `1=artist`, `3=copyright`, `4=character`, `5=meta`. **Exclude all rows where `category=1` (artist)** — artist tags are handled by a separate system.
- `post_count` — number of posts using the tag. **Keep this** — it's the primary ranking signal for suggestions.
- `created_at`, `updated_at` — not needed for the autofill feature, safe to drop.
- `is_deprecated` — boolean-like flag. Deprecated tags should be excluded from the final search index, but see the alias step below before discarding this info.
- `word` — a normalized/searchable variant of `name` (spaces instead of underscores, sometimes without parenthetical qualifiers). Use this as the primary field for prefix matching against user input.

There is also a companion `tag_aliases.csv` with (at minimum) `antecedent_name` and `consequent_name` columns, mapping deprecated/alternate tag names to their canonical replacement.

## What to build

### 1. Data processing (one-time / re-runnable script)

Write a script that:
1. Loads `tags.csv`.
2. Drops rows where `category == 1` (artist).
3. Drops rows where `is_deprecated` is true **only after** cross-referencing `tag_aliases.csv` — build an alias map (`old_name → canonical_name`) from deprecated tags so a search hit on an old name can still resolve to the current tag.
4. Keeps only the columns actually needed at runtime: `name`, `category`, `post_count`, `word`.
5. Outputs a compact local store optimized for prefix search — prefer **SQLite** with an index on `word` (and `name`) if the project already touches a filesystem/backend; otherwise a sorted JSON/array structure loaded into memory client-side is fine for a browser-only app. Pick whichever fits the existing stack from Step 0.
6. Also persists the alias map (`old_name → canonical_name`) alongside the main tag store.

### 2. Autofill/search logic

Implement a lookup function that, given partial user input:
1. Normalizes the query the same way `word` is normalized (spaces vs underscores, lowercase).
2. Finds tags where `word` (or `name`) starts with the query (prefix match). Also match against alias entries and resolve them to the canonical tag.
3. Sorts matches by `post_count` descending.
4. Returns the top N results (default 10–20, make it configurable) with `name`, `category`, and `post_count` included, so the UI can render a category badge/color and optionally show popularity.
5. Should be fast enough for keystroke-by-keystroke querying — debounce input on the UI side (~150–250ms) and make sure the lookup itself is indexed, not a linear scan over the full tag list.

### 3. UI integration

1. Hook this into the existing search/prompt input component identified in Step 0.
2. Show a dropdown/listbox of suggestions below the input as the user types (minimum 2–3 characters before querying, to avoid noisy results on 1 character).
3. Each suggestion row should show the tag name (human-readable, underscores replaced with spaces is fine for display) and a small colored badge/label for its category (suggested convention, matching Danbooru's own site colors):
   - general → blue
   - copyright → purple/violet
   - character → green
   - meta → yellow/gray
4. Selecting a suggestion (click or keyboard Enter/Tab) inserts the **canonical `name`** (underscore form) into the input, not the display form.
5. Support keyboard navigation (up/down arrows, enter to select, escape to dismiss) consistent with how the rest of the project handles similar dropdowns/comboboxes, if such a pattern already exists.

## Non-goals / explicitly out of scope

- Artist tags — handled separately, do not include `category=1` in this feature.
- Fetching data live from Danbooru — this is a fully local/offline dataset, no network calls at runtime.
- Tag translation/localization — not part of this task unless asked.

## Deliverables

1. The data processing script (re-runnable if I refresh the CSV later).
2. The generated local store (SQLite file or serialized JSON, whichever was chosen).
3. The autofill lookup function/module.
4. UI wiring into the existing search input, with category badges and keyboard support.

Ask me before making structural changes outside the search/autofill feature itself.
