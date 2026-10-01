# F004 — Commit 8936386: the AND/OR support the ticket refers to

- **Queries**: Q008 (git log on pgsql.pyx + pgsql_parser.rs), git show 8936386

## Digest

`8936386 "support OR and AND operations in jsonb filters"` (2026-09-25,
Jesus Lara) is the direct precedent. It added:

- `@>|` token to `JSONB_OPERATORS` in BOTH builders
- `JSONB_KEY_SUFFIXES` stripping in both
- helper `jsonb_any_of_condition` in both (Cython + Rust)
- one dispatch branch in each `jsonb_condition`
- 53 lines of parametrized tests in `tests/test_pgsql_jsonb_filters.py`
- version bump in `querysource/version.py`

Files touched: `querysource/parsers/pgsql.pyx` (+39), `rust/src/pgsql_parser.rs`
(+42), `tests/test_pgsql_jsonb_filters.py` (+53), `querysource/version.py`.

This is the exact template for adding `@!` / `@$`: token in the two
JSONB_OPERATORS constants + one helper + one dispatch branch per builder +
dual-path tests. "AND" in that commit message = implicit multi-key containment
(`{"status": "active", "level": 2}` → single `@>` with a two-key JSON doc) and
the conjunctive array form of `@>`; "OR" = `@>|`.

Related precedent: ec68025 (TASK-769, ILIKE/NOT ILIKE dict operator,
Rust + Cython) — same dual-builder discipline.

## Citations
- git 8936386 (diff summarized above)
- git ec68025 (TASK-769)
