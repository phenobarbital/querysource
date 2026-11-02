# Design research triage — semantic-layer (FEAT-175)

Model: `gpt-5.6-luna` · codex-cli 0.160.0 · reasoning high · 2026-10-14T17:29:30Z → 17:31:08Z · exit 0
Brief: `brief.md` (accepted brainstorm only) · Output: `suggestions.json` (12 suggestions, schema-valid)
All 48 `affected_paths` entries passed repository containment and `test -e`.

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|
| S1 | Resolve semantic type and datasource before provider construction (architecture) | CONFIRM (partially) | Dispatch keys on `QueryModel.provider`, not on `kind` in `query_raw`; but `QueryService` enforces `datasource:use` on the literal `'semantic'` (`querysource/handlers/service.py:347-363`), so the provider enforces `datasource:use`/`driver:use` on the resolved datasource itself. | §2 Overview, M5 `enforce_datasource`, AC-21 |
| S2 | Propagate principal identity into semantic compilation and MultiQS children (risk) | CONFIRM + ESCALATE | `QS.build_provider()` never passes `self._principal` to providers (`querysource/queries/qs.py:303-311`); slug-branch args gain `principal`. MultiQS child propagation escalated to §8 Q13; v1 fail-closed. | M7, AC-25, §8 Q13 |
| S3 | Key caches by compiled parameters, identity and external model content (risk) | CONFIRM | `BaseProvider.checksum()` hashes only `_query` (`querysource/providers/abstract.py:330`); `result_cache_key` adds store/slug/revision only (`querysource/cache_identity.py:65`). Provider checksum folds `model_version` (resolved bytes digest), dialect, connection. | M6 `checksum`, AC-23 |
| S4 | Define how CompiledQuery parameters reach asyncdb (api) | CONFIRM | Execution is `conn.query(self._query)` with no binding (`querysource/providers/default.py:123`); `sql` is literal SQL, `parameters` carries the canonical request only. | §2 Overview, M6, AC-24 |
| S5 | Normalize and reject transport conditions before semantic validation (api) | CONFIRM | Already M1 `from_conditions`; added body-vs-query precedence (`{**options, **params}`, `service.py:295`). | M1, §7 |
| S6 | Make the catalog endpoint tenant-aware and field-redacting (risk) | CONFIRM | Tenant awareness via `QueryDescribe._store/_load_visible` (`querysource/handlers/describe.py:83-143`); explicit redaction of denied fields added. | M9, AC-26 |
| S7 | Preserve the legacy dry_run tuple while exposing the object result separately (api) | CONFIRM | `QS.dry_run()` and `QueryService.test_slug()` unpack `(result, error)`; tuple kept, `semanticProvider.compiled` + `api.compile()` expose the object. | M6, AC-6 |
| S8 | Make policy layers conjunctive and preserve fail-closed error semantics (risk) | CONFIRM | `QS.build_provider()` wraps provider errors into `QueryError(500)` unless `ParserError` (`qs.py:318-325`); `SemanticError` base + re-raise added; layers declared conjunctive. | M1, M7, AC-22 |
| S9 | Constrain directory-backed model paths and hash resolved bytes (risk) | CONFIRM | Already M3 (realpath containment, digest); added `QS_SEMANTIC_MAX_MODEL_BYTES` and `yaml.safe_load` only. | M3, M7, §7 |
| S10 | Keep the optional compiler dependency behind the lazy provider boundary (architecture) | CONFIRM | Already the design (`load_provider`, lazy import); fake-repository tests added. | M4, M6, AC-3/AC-4 |
| S11 | Use an explicit dialect registry rather than inferring from class names (architecture) | CONFIRM | Already M2 `DRIVER_DIALECTS` with non-SQL rejection and ANSI warning; table test covers every `SUPPORTED` key. | M2, §7, AC-5 |
| S12 | Test semantic behavior through every existing execution surface (testing) | CONFIRM | Tests added for tenant catalog route, legacy dry-run tuple, principal pass-through, checksum, datasource PBAC, MultiQS child fail-closed. | §4 |

Summary: **12** confirmed (S1 partially; S2 also escalated) · **0** rejected · **1** escalated (S2 → §8 Q13).
