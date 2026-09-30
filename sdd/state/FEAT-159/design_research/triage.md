# Design research triage — onedrive-multiqs-source (FEAT-159)

Model: `gpt-5.6-luna` · codex-cli 0.159.0 · exit 0 · 10 suggestions, schema-valid.
Evidence check: every `affected_paths` entry passed the containment check and `test -e`.
Spot-checked claims: `tests/test_source_registry.py:35` asserts `len(SOURCE_REGISTRY) == 5`
(true), and `sharepoint.py:208-210` patches `platform.version` without restoring it (true).
The S6 claim, that `SchedulerJobsView.post` only re-syncs while definitions are written in
`QueryManager` (`manager.py:463/718/834`), was confirmed by reading `handlers/scheduler.py:208-275`.

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|
| S1 | Add a first-class link-only backend contract (architecture) | CONFIRM | `ExternalAuth.configure` registers login routes (`external.py:114-157`). The navigator-auth prerequisite must be link-only, and querysource must add a test that no `/auth/onedrive/login` route exists. | §2 Integration Points (P1), §5 AC, §7 |
| S2 | Remove the ambiguous Azure provider fallback (api) | CONFIRM | The proposal's optional `provider: azure` would refresh through the corporate SSO backend. The provider is fixed to `onedrive`, and the explicit `auth: app/delegated` mode matrix rejects invalid combinations. | §2 Overview, §3 M5, §5 AC |
| S3 | Explicit pre-thread credential phase + token adapter (architecture) | CONFIRM | `GraphServiceClient` accepts only a `TokenCredential`/`AsyncTokenCredential` (verified signature). The spec adds `ThreadSource.prepare()` and `StaticTokenCredential`. | §3 M1, M4 |
| S4 | Inject scheduler auth context instead of request state (architecture) | CONFIRM | `scheduled_multiqs_job` builds `MultiQS(slug, tenant)` (`jobs.py:144`) and has no app. `QSScheduler.startup` keeps `app["auth"]`. Job kwargs carry `run_as_user_id` plus the non-secret auth-service reference, following the `notification_manager` object precedent. No tokens go into kwargs. | §3 M7 |
| S5 | Extend persistence boundary + tolerate old schemas (risk) | CONFIRM (partial) | Adopted: tolerant `schedulable()`/`get_run_as()` that read NULL on an un-migrated store, plus fixture and DDL-gate updates. Rejected: adding the field to both models. The user decided the field is repository-only, so it stays out of every generic write path. | §3 M6, §7 |
| S6 | Assign run-as in the definition transaction, not scheduler sync (risk) | CONFIRM | Capturing it in `SchedulerJobsView.post` would let anyone who can re-sync a slug take over its identity. It is set only when `attributes.scheduler` is created or changed through `QueryManager` (patch/upsert), with the column update and audit row in the same repository transaction. It is rejected from payloads, and ordinary edits keep it. | §2 Overview, §3 M6/M7, §5 AC, §8 |
| S7 | Make audit immutability enforceable by the database (risk) | CONFIRM | The DDL adds a BEFORE UPDATE OR DELETE trigger, REVOKE UPDATE/DELETE/TRUNCATE for the app role, and a per-store `{table}_run_as_audit` table, with rollout for public and tenant stores. | §3 M6, §7 |
| S8 | Test Graph contract + preserve SharePoint behaviour (testing) | CONFIRM | The spec adds mocked Graph builder tests for every mode, keeps `tests/test_source_sharepoint.py` unchanged, and updates the registry-size assertion deliberately. | §4 |
| S9 | Avoid leaking a process-wide platform.version patch (risk) | CONFIRM | The patch becomes a scoped context manager, reference-counted under a lock and restored in `finally`. Only credentials the base created are closed. | §3 M1, §7 |
| S10 | Keep introspection-visible field access (testing) | CONFIRM | `extract_source_schema` regex-walks `__init__` across the MRO (`_introspect.py:990-1013`). Literal `creds.get`/`source.get`/`options.get` calls stay in the constructors, and there is a regression test that `SharepointSource`'s schema is unchanged. | §3 M1/M2/M5, §4 |

Summary: **10** confirmed (1 partial) · **0** rejected · **0** escalated.
