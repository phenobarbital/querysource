# Code Review: FEAT-182 — querysource-management-pbac (TASK-943..948)

**Spec**: sdd/specs/querysource-management-pbac.spec.md
**Branch**: `feat-FEAT-182-querysource-management-pbac` (reviewed at `752b7f6`)
**Reviewed files**:
- `querysource/handlers/{manager,query_moves,query_stores,service,describe}.py`
- `querysource/{conf,services}.py`
- `policies/defaults.yaml`, `policies/HARDENING.md`
- docs (`DESCRIBE_API.md`, `PBAC_PROGRAMMATIC.md`, `CHANGES.rst`)
- 7 test files

**Overall verdict**: ⚠ Approved with notes (one style fix applied in review; version bump left to `/release`)

---

## Summary

The production diff is small and matches the spec:
- **Management gate:** one helper, `_enforce_definition_access`, applied to every management verb, plus the move route and the store catalogue.
- **QueryManager.get:** the gate sits outside the catch-all, so a denial is a 404, not a 500.
- **Driver route:** `driver:query` on the driver.
- **Test endpoint:** `raw_query:execute` and `driver:use` on `POST /api/v2/test/queries`.
- **Flags:** two flags whose defaults keep today's behaviour.

Tests use the real `PolicyEvaluator` through `setup_pbac`. The worker mutation-checked each denial.

## Findings

### 🔴 Critical
- none

### 🟡 Major
- none

### 🟢 Minor / Suggestions
- **[querysource/handlers/describe.py]** `_execute_fallback()` was followed directly by `VOCABULARY_LINK` with no blank lines (PEP 8 E305). **Fixed in review.**
- **[querysource/handlers/manager.py `post` / `put`]** The gate runs after body parsing, because the slug may come from the body. A denied caller without a body gets 406 before 404. No data leaks; accepted.
- **[tests/integration/test_management_pbac_matrix.py]** The plain-role `driver:pg` row stays green with the driver branch removed: an empty slug name was already a 404, so the row cannot tell the difference. The `driver:http` row catches the mutation. Accepted; reported by the worker.
- **[spec AC]** "Version bumped to 5.3.0" is deferred to `/release` (TASK-948 scope; `.bumpversion.cfg` is stale at 3.6.0, `querysource/version.py` says 5.2.4).

## Deferred Findings

| Severity | Title | Issue ID | Filed By |
|----------|-------|----------|----------|
| none     | n/a (no confirmed 🔴/🟡) | n/a | n/a |

## Acceptance Criteria Check

| Criterion | Status | Notes |
|-----------|--------|-------|
| No `slug:view` → no definition / `query_raw` via management (404) | ✅ | `test_management_pbac.py` |
| No `slug:manage` → no create/update/patch/delete/move, repo untouched | ✅ | management + moves tests |
| `POST /api/v2/test/queries` needs `raw_query:execute` (+ `driver:use`) | ✅ | `test_queryservice_driver_rawquery_pbac.py` |
| Driver route decided by `driver:query`; admin allowed; http vs pg | ✅ | same file + matrix |
| `QS_DESCRIBE_EXECUTE_FALLBACK=false` removes the fallback | ✅ | `test_describe_fallback_flag.py` |
| `QS_PBAC_STRICT_BOOT=true` aborts a failed bootstrap | ✅ | `test_pbac_strict_boot.py` |
| PBAC off → unchanged | ✅ | `test_querymanager_pagination.py`, PBAC-off cases |
| `defaults.yaml` admin has the 3 actions | ✅ | `test_default_policies_load.py` |
| Docs + CHANGES | ✅ | |
| Version 5.3.0 | ⏳ | `/release` |
| Targeted suites green | ✅ | 120 passed, 1 xfailed, run by the reviewer in the worktree |

Pre-existing failures, confirmed on untouched `dev` by the worker:
- `tests/auth/test_identity_tokens.py` (4);
- `tests/policies/test_authorized_policy.py` (6);
- `TestPbacOff` in `tests/integration/test_pbac_enforcement.py` (needs Postgres);
- `tests/handlers/test_airtable_oauth.py` (missing `aioresponses`).

## Adversarial Cross-Check (codex `review --base <merge-base>`)

| Finding | Disposition | Reason |
|---------|-------------|--------|
| "No actionable defects" | — | Codex's sandbox blocked sockets and template writes, so it ran only the policy tests (7 passed, 1 xfail). The HTTP and startup suites it could not run were run by the reviewer: 120 passed. |

## Positive Highlights
- Every gate reuses `enforce_request_pbac` / `_enforce_pbac`; there is no new evaluation logic.
- The PBAC-off fast path stays a no-op, with no extra DB reads.
- The integration matrix runs the real `QuerySource.setup()` with PBAC on.
