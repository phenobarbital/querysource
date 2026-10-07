---
id: F014
query_id: Q013
type: tree
intent: parser package layout, Rust extension layout, build toolchain
executed_at: 2026-10-07T00:18:00Z
duration_ms: 200
parent_id: null
depth: 0
---

# F014 — Two parallel implementations per dialect (Cython `.pyx` + Rust `.rs`), both must change together

## Summary

`querysource/parsers/` has 16 Cython `.pyx` parsers (+ `.pxd`, generated `.c`, built
`.so` for cp310-312). `rust/src/` has one `*_parser.rs` per dialect compiled into a
single `_qs_parsers` PyO3 module loaded by `querysource/qs_parsers/__init__.py`
(`HAS_RUST` flag). `sql.pyx`/`pgsql.pyx` import `_qs_parsers as _rs` and guard with
`HAS_RUST`. Build: `make build-rust` (maturin develop) and `make stage-rust`. `cargo` and
`.venv/bin/maturin` exist on this machine. Wiki (`wikitoolkit`) is **not installed** in
`.venv`, so wiki queries were unavailable for this run.

## Citations

- path: `querysource/parsers/` — `abstract.pyx, sql.pyx, pgsql.pyx, sqlserver.pyx, bigquery.pyx, cql.pyx, mongo.pyx, elastic.pyx, arangodb.pyx, rethink.pyx, sosql.pyx, influx.pyx, deltatbl.pyx, iceberg.pyx, jsonb_unnest.pyx, parser.pyx`
- path: `rust/src/` — `lib.rs, filter_common.rs, sql_parser.rs, pgsql_parser.rs, mssql_parser.rs, bigquery_parser.rs, cql_parser.rs, soql_parser.rs, flux_parser.rs, mongo_parser.rs, rethink_parser.rs, arangodb_parser.rs, elastic_parser.rs, pgsql_unnest.rs, validators.rs, safe_dict.rs, sql_guard.rs, parseqs.rs`
- path: `querysource/qs_parsers/__init__.py`
  lines: 10-28
  symbol: `HAS_RUST`
- path: `querysource/parsers/pgsql.pyx`
  lines: 20-24
  symbol: `from querysource.qs_parsers import _qs_parsers as _rs`
- path: `Makefile`
  lines: 63-84
  symbol: `build-rust`, `stage-rust`
- path: `rust/src/validators.rs`
  lines: 10-12
  symbol: `field_components` regex (key suffix tokens `| & ! ~ #`)
