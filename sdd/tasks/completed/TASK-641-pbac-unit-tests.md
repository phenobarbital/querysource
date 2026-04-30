# TASK-641: PBAC unit tests — auth package

**Feature**: pbac-support
**Spec**: `sdd/specs/pbac-support.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-628, TASK-629, TASK-630
**Assigned-to**: unassigned

---

## Context

Implements the unit-test slice of **Module 15** of the spec. Each preceding
task ships its own targeted unit tests inline (TASK-628 has resolver tests,
TASK-629 has bootstrap tests, TASK-630 has helper tests). This task's job
is to **fill the gaps** the spec's Test Specification table calls out and
ensure the suite as a whole gives high confidence.

Specifically: extend the existing test files (do not duplicate) to cover
all rows in the spec's "Unit Tests" table that aren't already covered.

---

## Scope

Inspect what's already in `tests/auth/` and `tests/handlers/` after the
prior tasks complete. Then fill in any missing rows from the spec's Unit
Tests table:

| Test (from spec §4) | File to extend |
|---|---|
| `test_resolver_per_user_full_set` | `tests/auth/test_credentials.py` (TASK-628) |
| `test_resolver_per_user_partial_set` | same |
| `test_resolver_profile_from_policy` | same |
| `test_resolver_default` | same |
| `test_resolver_sanitization` | same |
| `test_resolver_no_session` | same |
| `test_setup_pbac_disabled` | `tests/auth/test_pbac_bootstrap.py` (TASK-629) |
| `test_setup_pbac_idempotent` | same |
| `test_enforce_pbac_disabled_noop` | `tests/handlers/test_abstract_pbac_helpers.py` (TASK-630) |
| `test_enforce_pbac_deny_raises_404` | same |
| `test_get_user_session_caches` | same |

Add tests that the inline scaffolds skipped, and add a few extras the spec
hints at:

- `test_resolver_logs_partial_set_warning_once` — verify dedup behaviour.
- `test_setup_pbac_credential_resolver_registered_when_idempotent` —
  even when reusing a pre-existing Guardian, the credential resolver must
  be registered.
- `test_enforce_pbac_passes_request_into_evalcontext` — verify the
  request is forwarded so policy environment conditions
  (e.g. `is_business_hours`) can read request attributes.
- `test_get_user_session_returns_none_when_navigator_session_missing` —
  `RuntimeError` path returns None (not raise).

**NOT in scope**: handler-level integration tests (TASK-642), perf test
(TASK-643), driver tests (already in TASK-636 and TASK-637).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/auth/test_credentials.py` | EXTEND | Add gap tests + log-dedup test. |
| `tests/auth/test_pbac_bootstrap.py` | EXTEND | Add idempotent-resolver-registration test. |
| `tests/handlers/test_abstract_pbac_helpers.py` | EXTEND | Add EvalContext-forwarding + missing-session tests. |
| `tests/conftest.py` | MODIFY | Add shared fixtures (see below). |

---

## Codebase Contract (Anti-Hallucination)

Use the verified imports and signatures already established in TASK-628,
TASK-629, TASK-630. No new codebase contracts here — this task lives
strictly in the test layer.

### Reusable fixtures

Add to `tests/conftest.py` (or `tests/auth/conftest.py` if it exists):

```python
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def session_factory():
    """Factory: build a SessionData-like dict with given userinfo."""
    def _build(username="alice", groups=("analysts",), service=False, **extra):
        return {
            "username": username,
            "user_id": username,
            "groups": list(groups),
            "roles": [],
            "service": service,
            **extra,
        }
    return _build


@pytest.fixture
def mock_evaluator_allow():
    ev = MagicMock()
    ev.check_access = MagicMock(return_value=MagicMock(
        allowed=True, effect="ALLOW", matched_policy="P", reason=""))
    return ev


@pytest.fixture
def mock_evaluator_deny():
    ev = MagicMock()
    ev.check_access = MagicMock(return_value=MagicMock(
        allowed=False, effect="DENY", matched_policy="P", reason="denied"))
    return ev
```

### Does NOT Exist

- ~~A real navigator-auth instance in tests~~ — keep all tests
  mock-driven. The integration tests (TASK-642) use the real engine.
- ~~A `pytest-aiohttp` test client at this layer~~ — that's TASK-642's job.

---

## Implementation Notes

### Log-dedup test (resolver)

```python
def test_partial_set_warning_dedup(monkeypatch, caplog):
    """Same partial set hit twice → only one warning logged."""
    import logging
    from querysource.auth import CredentialResolver
    monkeypatch.setenv("PG_BOB_HOST", "h")  # only one of five
    resolver = CredentialResolver()
    with caplog.at_level(logging.WARNING):
        resolver.resolve("PG", {"username": "bob"})
        resolver.resolve("PG", {"username": "bob"})
    # Count distinct warning messages — should be exactly one occurrence
    # for that (tier, missing-key-set) tuple:
    relevant = [r for r in caplog.records
                if "PG_BOB" in r.message or "partial" in r.message.lower()]
    assert len(relevant) <= 1
```

### EvalContext-forwarding test

```python
async def test_enforce_pbac_passes_request_into_evalcontext(handler):
    request = MagicMock()
    captured_ctx = []
    evaluator = MagicMock()
    def capture(ctx, **kwargs):
        captured_ctx.append(ctx)
        return MagicMock(allowed=True)
    evaluator.check_access = capture

    request.app = {"security": MagicMock(), "policy_evaluator": evaluator}
    request.get = lambda k, d=None: d
    request.__setitem__ = MagicMock()

    with patch("querysource.handlers.abstract.get_session",
               AsyncMock(return_value={"user": {"username": "alice"}})):
        await handler._enforce_pbac(
            request, resource_type="slug",
            resource_name="x", action="slug:execute",
        )

    # Verify the EvalContext got the request:
    assert captured_ctx
    ctx = captured_ctx[0]
    # EvalContext is dict-like; key "request" is set in its __init__:
    assert ctx.get("request") is request or ctx["request"] is request
```

### Idempotent resolver registration test

```python
def test_idempotent_keeps_existing_security_but_adds_resolver(aiohttp_app, tmp_path):
    """Pre-existing Guardian is reused; credential_resolver still registers."""
    pre_guardian = MagicMock()
    pre_evaluator = MagicMock()
    aiohttp_app["security"] = pre_guardian
    aiohttp_app["policy_evaluator"] = pre_evaluator
    # No credential_resolver in app yet.
    from querysource.auth import setup_pbac
    pdp, ev, guard = setup_pbac(aiohttp_app, policy_dir=str(tmp_path))
    assert aiohttp_app["security"] is pre_guardian   # reused
    assert aiohttp_app["policy_evaluator"] is pre_evaluator
    assert "credential_resolver" in aiohttp_app     # added
```

### Key Constraints

- **Mock-only** at this layer.
- **No skip without justification.** If a test can't be written without a
  real engine, document why and move it to TASK-642.
- **Use `caplog` fixture** for log-dedup verification — don't poke
  `logging.getLogger` internals.

### References in Codebase

- `tests/auth/test_credentials.py` (TASK-628 wrote this).
- `tests/auth/test_pbac_bootstrap.py` (TASK-629).
- `tests/handlers/test_abstract_pbac_helpers.py` (TASK-630).

---

## Acceptance Criteria

- [ ] Every row in the spec's "Unit Tests" table (§4) has a passing test.
- [ ] `test_partial_set_warning_dedup` passes — repeat partial sets log
      at most once.
- [ ] `test_enforce_pbac_passes_request_into_evalcontext` passes —
      EvalContext receives the live request.
- [ ] `test_idempotent_keeps_existing_security_but_adds_resolver` passes
      — the credential resolver is always registered, even on idempotent
      re-entry.
- [ ] `pytest tests/auth/ tests/handlers/ -v` clean.
- [ ] Full suite `pytest tests/ -x -q` clean.

---

## Test Specification

See Implementation Notes — all tests live in the listed extension files.

---

## Agent Instructions

1. Read spec §4 (Unit Tests table) and §6 (Codebase Contract).
2. Verify TASK-628, TASK-629, TASK-630 are in `tasks/done/`.
3. Inspect what's already covered in `tests/auth/` and
   `tests/handlers/`.
4. Fill in the gaps. Do not duplicate existing tests.
5. Add the shared fixtures to `tests/conftest.py`.
6. Run `pytest tests/auth/ tests/handlers/ -v`.
7. Run full suite.
8. Move task to `done/` and update the index.

---

## Completion Note

**Completed by**: Claude Sonnet 4.6 (SDD Worker)
**Date**: 2026-04-30
**Tests added (count)**: 7 new tests across 4 files (1 new file: tests/conftest.py)

**Deviations from spec**:

- `test_none_tuple_does_not_populate_app_keys`: Rather than calling `setup_pbac` with a non-existent path (which succeeds with 0 policies in the real implementation), the test uses `patch.dict(sys.modules, ...)` to block navigator-auth imports, triggering the ImportError path that returns `(None, None, None)`. This correctly tests the "bootstrap failure" scenario the spec intends.

- `test_module_does_not_eager_import_navauth` (pre-existing, modified): Added `finally` block to restore navigator-auth from sys.modules snapshot after the test. This was necessary to prevent test contamination that caused downstream tests calling `setup_pbac` or `_enforce_pbac` to fail with the upstream `Permission` dataclass ordering bug when navigator-auth was re-imported fresh.

- 119 tests pass, 1 xfailed (PolicyLoader YAML schema incompatibility for `test_default_policies_load.py::test_yaml_policies_loadable` — pre-existing from TASK-639).
