---
id: F012
query_id: Q011
type: git_log
intent: recent activity on the WHERE builders (90 days)
executed_at: 2026-10-07T00:16:00Z
duration_ms: 200
parent_id: null
depth: 0
---

# F012 — Builders are under active change: FEAT-152 (ILIKE dict op) and JSONB operator work in the last 2 weeks

## Summary

16 commits in the last 90 days on `sql.pyx`, `pgsql.pyx`, `pgsql_parser.rs`,
`sql_parser.rs`, `filter_common.rs`; all by Jesus Lara. Dominant themes: FEAT-152 qsurl
ILIKE dict operator (Rust + Cython, TASK-769), the `is_valid()` pre-quoting invariant
docs, and FEAT-153/JSONB operator groups (`@>|`, `@!`, `@$`). The ILIKE work is the direct
template for this feature.

## Citations

- commit: `ec68025a` 2026-09-24 — feat(qsurl-parser): TASK-769 — PostgreSQL ILIKE / NOT ILIKE dict operator (Rust + Cython)
- commit: `f313e115` 2026-09-25 — docs(qsurl-parser): pin the is_valid() pre-quoting invariant the TASK-769 ILIKE fix depends on
- commit: `acb6bdf5` 2026-09-25 — fix(qsurl-parser): resolve deferred code-review findings (issue:48c9b3050a0c, issue:2241b8e60919)
- commit: `89363869` 2026-09-25 — support OR and AND operations in jsonb filters
- commit: `4d57a4af` 2026-10-01 — fix(jsonb-not-or-operators): accept only list operands in Cython for Rust parity
- commit: `a4536ff1` 2026-09-30 — another fix on filter parser and empty results
- commit: `366c03b3` 2026-09-24 — build(rust): bump pyo3 to 0.29 and maturin floor to 1.15
