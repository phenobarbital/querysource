# F001 — Wiki orientation: JSONB where_cond filtering is dual-implemented

- **Queries**: Q001, Q002 (`wikitoolkit query`), Q003 (`wikitoolkit page`)
- **Wiki pages**: `file:querysource/parsers/pgsql.pyx` (0.94), `sym:rust/src/pgsql_parser.rs#pgsql_filter_conditions` (0.71), `file:tests/test_pgsql_jsonb_filters.py` (0.71)

## Digest

JSONB filter conditions for PostgreSQL are implemented twice, deliberately:

1. **Rust fast-path**: `rust/src/pgsql_parser.rs` → `pgsql_filter_conditions()`
   (L625-664), exported via the `querysource.qs_parsers._qs_parsers` PyO3
   extension; rayon-parallel.
2. **Cython fallback**: `querysource/parsers/pgsql.pyx` →
   `pgSQLParser._filter_conditions_cy()`; `filter_conditions()` (pgsql.pyx:209-217)
   tries Rust first and falls back to Cython on any exception.

Tests exercise BOTH paths with the same parametrized cases:
`tests/test_pgsql_jsonb_filters.py` (`PATHS = ["rust", "cython"]`, rust skipped
when `qs_parsers` not built).

Precedent for adding a new dict operator across both builders:
`sdd/tasks/completed/TASK-769-pg-ilike-dict-operator.md` (ILIKE / NOT ILIKE).

## Citations
- querysource/parsers/pgsql.pyx:209-217 (Rust-first dispatch, Cython fallback)
- rust/src/pgsql_parser.rs:625-664 (pgsql_filter_conditions)
- tests/test_pgsql_jsonb_filters.py (dual-path parametrization)
