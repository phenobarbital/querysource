# F003 — Rust twin implementation (must stay in lockstep)

- **Queries**: Q005 (grep), Q007b (read rust/src/pgsql_parser.rs:200-349)

## Digest

`rust/src/pgsql_parser.rs` mirrors the Cython implementation 1:1:

- `JSONB_OPERATORS: &[&str] = &["@>", "<@", "@>|", "->", "->>"]` — L82
- `JSONB_KEY_SUFFIXES: &[char] = &['|', '!', '~', '#', '@', ':']` — L86
- `jsonb_any_of_condition()` — L219-235 (OR-ed containment, same contract as
  the Cython helper)
- `jsonb_path_condition()` — L242-274
- `jsonb_condition()` — L290-340: same dispatch, same first-key semantics,
  same `operators != dict.len()` drop rule; column validated by
  `pg_safe_identifier_key` (L320) after suffix strip.
- Entry point `pgsql_filter_conditions()` — L625-664: extracts entries under
  the GIL, renders conditions in parallel (rayon), joins with AND via
  `apply_pg_where_clause`.

Because `filter_conditions()` prefers Rust and silently falls back to Cython
(pgsql.pyx:209-217 — `except Exception: pass`), an operator added only in
Cython would appear to "not work" whenever the Rust extension is built:
the Rust path would drop/mis-render it WITHOUT raising, so no fallback
occurs. → Both builders MUST be extended in the same change, and the Rust
crate rebuilt (`maturin develop`) before tests.

## Citations
- rust/src/pgsql_parser.rs:82, 86, 219-235, 242-274, 290-340, 625-664
- querysource/parsers/pgsql.pyx:209-217
