---
id: F011
query_id: Q010
type: read
intent: how FEAT-152 tested dict operators on both Rust and Cython paths
executed_at: 2026-10-07T00:15:00Z
duration_ms: 300
parent_id: null
depth: 0
---

# F011 — Dual-path parametrized tests with skip-if-stale-extension guard

## Summary

`tests/qsurl/test_pg_ilike.py` builds a `pgSQLParser(definition=None,
conditions=QueryObject(query_raw=SQL), query=SQL)`, sets `parser.filter`, and renders
through either `pgsql._rs.pgsql_filter_conditions` (rust) or
`parser._filter_conditions_cy` (cython) via `pytest.param` with a `skipif` when the
installed `.so` predates the Rust change. `tests/test_rust_parsers.py::TestPgsqlIlikeOperator`
probes the extension and `pytest.skip`s. Memory note: Python loads the source-tree
`_qs_parsers` `.so`, so `make build-rust` then `make stage-rust` is required for tests to
see Rust changes. `cargo` and `.venv/bin/maturin` are available locally.

## Citations

- path: `tests/qsurl/test_pg_ilike.py`
  lines: 1-60
  symbol: `_rust_supports_ilike`, `PATHS`, `_make_parser`, `_render`
- path: `tests/test_rust_parsers.py`
  lines: 576-593
  symbol: `TestPgsqlIlikeOperator.test_ilike_dict_operator`
- path: `tests/e2e/test_qsurl_dry_run.py`
  lines: 1
  symbol: (end-to-end qsurl → QS dry-run, TASK-776)
- path: `Makefile`
  lines: 63-84
  symbol: `build-rust`, `stage-rust`
