# Design research triage — FEAT-180

Model: gpt-5.6-luna · all affected_paths verified inside the repository and existing.

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|
| S1 | Resolve case sensitivity before adding aliases — qsurl maps bare `startswith`/`contains`/`endswith` to ILIKE while the table makes them case-sensitive (api) | ESCALATE | U1 is a requester decision (both variants, plain = sensitive) and the proposal wins over the reviewer; the entry points never share a code path, but the cross-entry-point meaning of the word differs — the human decides whether qsurl is re-mapped later | §8 Q3, §2 vocabulary note, §7 risk |
| S2 | Define an explicit JSON-dict collision policy for PG containment and BigQuery `JSON_VALUE` (api) | CONFIRM | real behaviour change for keys literally named like an operator; policy fixed: table names win on every dialect, alternatives documented | §2 reserved-name note, §7 risk, §5 AC8 |
| S3 | Enforce single-key partial-match dictionaries (first-vs-last entry divergence) (api) | CONFIRM | Rust reads the first entry, `_where_element` the last; multi-key dicts with a table name are now rejected in M3 and in every Cython builder | §2 stage 1, §3 M1 `validate_partial_match_dict`, §5 AC14, §4 tests |
| S4 | Do not rely solely on `_where_element`; validation must be a reusable non-rendering validator invoked before dispatch and from the lifecycle (risk) | CONFIRM | exactly the M1 validator + M3 pre-dispatch + builder re-validation design; Rust `Err` → Cython fallback → `ParserError` is the uniform outcome (M5 adds the missing `SQLParser` wrapper) | §2 stage 3, §3 M1/M3/M5, §5 AC6/AC10 |
| S5 | Make escaping ownership explicit per operator — ready pattern vs raw value (risk) | CONFIRM | pinned by the table's `escape` flag; qsurl stays on the uppercase `ILIKE` branch so it cannot be double-escaped; escaping corpus on both paths | §2 escaping-ownership note, §4 escaping corpus, §5 AC7 |
| S6 | Replace generic-SQL escape assumptions with dialect capabilities or stage per dialect (architecture) | CONFIRM | the feature is staged per dialect (M4–M7) with one rendering helper per builder; the generic parser cannot know its dialect, so it uses the portable `!` escape char instead of a dialect-dependent default | §2 rendered forms notes, §3 M5–M7 |
| S7 | Avoid leaking SQL Server dict support into shared SOQL/CQL parsers via `filter_common` (architecture) | CONFIRM | `process_entry` returns `None` for the new `Dict` variant; only `process_mssql_entry` consumes it; cargo test pins SOQL/CQL output | §3 M6, §4 `test_shared_process_entry_ignores_dict`, §5 AC13 |
| S8 | Reuse the existing regex safety policy including the nested-quantifier rejection (risk) | CONFIRM | U3 asked for a bounded length; adding the residual.py nested-quantifier check keeps one policy for both entry points at no design cost | §3 M1 `NESTED_QUANTIFIER_RE`, §5 AC5, §7 risk |
| S9 | Build a full dialect × path × operator conformance matrix incl. errors, JSON collisions, multi-key input and extension staging (testing) | CONFIRM | added as a single parametrized matrix test on top of the per-dialect files; AC11 pins `make build-rust && make stage-rust` | §4 `test_conformance_matrix`, §5 AC11 |

Summary: **8** confirmed · **0** rejected · **1** escalated.
