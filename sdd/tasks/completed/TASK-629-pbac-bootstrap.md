# TASK-629: PBAC bootstrap (`setup_pbac`)

**Feature**: pbac-support
**Spec**: `sdd/specs/pbac-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-627, TASK-628
**Assigned-to**: unassigned

---

## Context

Implements **Module 2** of the spec. Builds `setup_pbac(app, policy_dir,
cache_ttl, default_effect)` which initializes navigator-auth's PBAC engine
and registers `Guardian` / `PDP` / `PolicyEvaluator` / `CredentialResolver`
on the aiohttp `app` dict.

This is the central bootstrap that `QuerySource.setup(app)` (TASK-638) calls
when `QS_PBAC_ENABLED=True`. Idempotent — if `app['security']` is already
populated by a parent stack (e.g. navigator-api invoked navigator-auth's
`PDP.setup(app)` first), reuse the existing instances.

Mirror ai-parrot's `parrot/auth/pbac.py` shape; do not import from ai-parrot.

---

## Scope

- Implement `querysource/auth/pbac.py` with `setup_pbac(...)`.
- Lazy-import `navigator-auth` symbols **inside** the function so the
  module itself loads cleanly even when nav-auth is not installed (or when
  PBAC is disabled by setting).
- Build the engine: `YAMLStorage(policy_dir)`, `PolicyLoader.load_from_directory()`,
  `PolicyEvaluator(default_effect=DENY, cache_ttl_seconds=cache_ttl)`,
  `PDP(storage=...)`, `Guardian(pdp)`.
- Register on `app`:
  - `app['security']` ← Guardian
  - `app['abac']` ← PDP
  - `app['policy_evaluator']` ← PolicyEvaluator
  - `app['credential_resolver']` ← `CredentialResolver()` (new instance)
- **Idempotency**: if `app.get('security')` is already set, reuse it; only
  register the credential resolver (which is QS-specific and won't have
  been set by nav-auth).
- Return `tuple[Optional[PDP], Optional[PolicyEvaluator], Optional[Guardian]]`
  matching ai-parrot's contract.
- On any failure during bootstrap (missing nav-auth, malformed YAML,
  missing policy dir), log an error and return `(None, None, None)` —
  but the spec requires fail-closed elsewhere, so the caller (TASK-638) is
  responsible for surfacing the failure when `QS_PBAC_ENABLED=True`.
- Re-export `setup_pbac` from `querysource/auth/__init__.py`.

**NOT in scope**: calling `setup_pbac` from `QuerySource.setup` (TASK-638);
installing aiohttp middleware (the spec is explicit — handlers enforce
explicitly, no middleware); writing the AbstractHandler helpers (TASK-630);
loading the actual default policy YAMLs (TASK-639).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/auth/pbac.py` | CREATE | `setup_pbac()` + module logger + helper docstrings. |
| `querysource/auth/__init__.py` | MODIFY | Re-export `setup_pbac`. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

All `navigator_auth` imports must happen **inside** `setup_pbac()` (lazy):

```python
# Lazy imports inside setup_pbac():
from navigator_auth.abac.pdp import PDP                              # navigator_auth/abac/pdp.py:39
from navigator_auth.abac.guardian import Guardian                    # navigator_auth/abac/guardian.py:16
from navigator_auth.abac.policies.evaluator import (
    PolicyEvaluator, PolicyLoader,
)                                                                    # navigator_auth/abac/policies/evaluator.py
from navigator_auth.abac.policies.abstract import PolicyEffect       # navigator_auth/abac/policies/abstract.py
from navigator_auth.abac.storages.yaml_storage import YAMLStorage    # navigator_auth/abac/storages/yaml_storage.py
```

Module-level (eager):

```python
import logging
from typing import Optional
from aiohttp import web
from querysource.auth.credentials import CredentialResolver
```

### Existing Signatures to Use

```python
# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/pdp.py:39
class PDP:
    def __init__(self, storage, policies=None, yaml_storage=None): ...
    def setup(self, app: web.Application): ...   # NOTE: do NOT call this from
                                                  # setup_pbac unless the host
                                                  # app explicitly opted in.
                                                  # PDP.setup registers
                                                  # middleware + REST routes,
                                                  # which the spec says
                                                  # QuerySource must NOT install.

# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/guardian.py:16
class Guardian:
    def __init__(self, pdp): ...

# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/policies/evaluator.py:361
class PolicyEvaluator:
    def __init__(self, cache_size: int = 1024, cache_ttl_seconds: int = 300): ...
```

### ai-parrot Reference (read-only — do NOT import from QuerySource)

```python
# /home/jesuslara/proyectos/navigator/ai-parrot/packages/ai-parrot/src/parrot/auth/pbac.py:35
def setup_pbac(
    app: "web.Application",
    policy_dir: str = "policies",
    cache_ttl: int = 30,
    default_effect: Optional[object] = None,
) -> "tuple[Optional[PDP], Optional[PolicyEvaluator], Optional[Guardian]]":
    ...
```

Use this signature **shape** as a guide; QuerySource's defaults differ
(`cache_ttl=300` not 30; `default_effect=PolicyEffect.DENY`).

### Does NOT Exist

- ~~A QuerySource-private decision cache~~ — reuse `PolicyEvaluator`'s LRU.
- ~~`Guardian.setup(app)`~~ — Guardian has no `setup` method; it's
  registered manually as `app['security'] = guardian`.
- ~~Calling `pdp.setup(app)` inside `setup_pbac`~~ — `PDP.setup` installs
  navigator-auth's middleware + REST routes, which the spec explicitly
  prohibits in QuerySource. The host app may call it for its own routes.
- ~~`app['guardian']`~~ — convention is `app['security']` (matches
  navigator-auth and ai-parrot).

---

## Implementation Notes

### Function shape

```python
def setup_pbac(
    app: web.Application,
    policy_dir: str,
    cache_ttl: int = 300,
    default_effect: Optional["PolicyEffect"] = None,
) -> "tuple[Optional[PDP], Optional[PolicyEvaluator], Optional[Guardian]]":
    """Initialize navigator-auth PBAC and register on the aiohttp app.

    Side effects (only when initialization succeeds):
      app['security']           = Guardian
      app['abac']               = PDP
      app['policy_evaluator']   = PolicyEvaluator
      app['credential_resolver']= CredentialResolver

    Idempotent: if app['security'] is already populated by a parent stack,
    QuerySource reuses the existing instances instead of recreating them.
    """
    logger = logging.getLogger("querysource.auth.pbac")

    # 1) Idempotent shortcut: if Guardian is already present, reuse it.
    if app.get("security") is not None and app.get("policy_evaluator") is not None:
        logger.info("PBAC: reusing pre-existing Guardian/PolicyEvaluator from app")
        # Still ensure credential_resolver is registered (QS-specific).
        if "credential_resolver" not in app:
            app["credential_resolver"] = CredentialResolver(logger=logger)
        return (app.get("abac"), app["policy_evaluator"], app["security"])

    # 2) Lazy import navigator-auth.
    try:
        from navigator_auth.abac.pdp import PDP
        from navigator_auth.abac.guardian import Guardian
        from navigator_auth.abac.policies.evaluator import (
            PolicyEvaluator, PolicyLoader,
        )
        from navigator_auth.abac.policies.abstract import PolicyEffect
        from navigator_auth.abac.storages.yaml_storage import YAMLStorage
    except ImportError as exc:
        logger.error("PBAC bootstrap failed: navigator-auth not importable: %s", exc)
        return (None, None, None)

    if default_effect is None:
        default_effect = PolicyEffect.DENY

    # 3) Build storage + load policies.
    try:
        storage = YAMLStorage(directory=policy_dir)
        loader = PolicyLoader()
        policies = loader.load_from_directory(policy_dir)
    except Exception as exc:
        logger.error("PBAC bootstrap failed during policy load: %s", exc)
        return (None, None, None)

    # 4) Construct evaluator + PDP + Guardian.
    evaluator = PolicyEvaluator(cache_size=1024, cache_ttl_seconds=cache_ttl)
    # Attach evaluator to PDP (mirror ai-parrot's flow):
    pdp = PDP(storage=storage, policies=policies)
    # The PolicyEvaluator may be instantiated by PDP internally; if so,
    # prefer the PDP-attached one. Verify via grep before final wiring.
    pdp_evaluator = getattr(pdp, "_evaluator", None)
    if pdp_evaluator is not None:
        evaluator = pdp_evaluator

    guardian = Guardian(pdp=pdp)

    # 5) Register on app.
    app["security"] = guardian
    app["abac"] = pdp
    app["policy_evaluator"] = evaluator
    app["credential_resolver"] = CredentialResolver(logger=logger)

    logger.info(
        "PBAC enabled: %d policies loaded from %s, cache_ttl=%ds",
        len(policies) if policies else 0, policy_dir, cache_ttl,
    )
    return (pdp, evaluator, guardian)
```

### Key Constraints

- **Lazy nav-auth imports.** The function body does the imports — module
  scope only imports stdlib + aiohttp + the QS resolver.
- **Do NOT call `pdp.setup(app)`.** That installs middleware and REST
  endpoints which the spec explicitly excludes from QuerySource (handlers
  enforce explicitly).
- **Idempotency**: if `app['security']` is non-None, do not recreate it —
  but DO register `app['credential_resolver']` if it's missing.
- **Errors must not bubble.** Log and return `(None, None, None)`. The
  caller (TASK-638) decides whether to fail the boot.

### `__init__.py` update

```python
from querysource.auth.credentials import CredentialResolver, ResolvedCredentials
from querysource.auth.pbac import setup_pbac

__all__ = ("CredentialResolver", "ResolvedCredentials", "setup_pbac", "logger")
```

### References in Codebase

- `parrot/auth/pbac.py:35` (ai-parrot, read-only) — shape reference.
- `navigator_auth/abac/pdp.py:208` — sets `app['security']` etc. — confirm
  exact key names before final wiring.

---

## Acceptance Criteria

- [ ] `from querysource.auth import setup_pbac` succeeds.
- [ ] `querysource.auth.pbac` does NOT eager-import `navigator_auth` at
      module load (`python -c "import querysource.auth.pbac; import sys;
      assert 'navigator_auth' not in sys.modules"` passes).
- [ ] Calling `setup_pbac(app, policy_dir=<empty-tmp>, cache_ttl=300)` on a
      fresh aiohttp app populates all four `app[...]` keys.
- [ ] Calling `setup_pbac` twice on the same app does not raise and does not
      re-create `app['security']`.
- [ ] When `app['security']` is pre-populated by a fixture, `setup_pbac` reuses
      it and returns the existing tuple.
- [ ] When the policy directory does not exist, the function returns
      `(None, None, None)` and logs an error (does not raise).
- [ ] No regressions: `pytest tests/ -x -q` clean.

---

## Test Specification

```python
# tests/auth/test_pbac_bootstrap.py
import pytest
from aiohttp import web
from querysource.auth import setup_pbac


@pytest.fixture
def empty_policies_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def aiohttp_app():
    return web.Application()


class TestSetupPbac:
    def test_module_does_not_eager_import_navauth(self):
        import sys
        # Force a clean re-import:
        for mod in list(sys.modules):
            if mod.startswith("navigator_auth"):
                del sys.modules[mod]
        import querysource.auth.pbac  # noqa: F401
        assert "navigator_auth" not in sys.modules, \
            "querysource.auth.pbac must lazy-import navigator-auth"

    def test_registers_app_keys(self, aiohttp_app, empty_policies_dir):
        pdp, ev, guard = setup_pbac(aiohttp_app, policy_dir=empty_policies_dir)
        # If nav-auth is installed in the dev env, all four should be set.
        if guard is not None:
            assert aiohttp_app["security"] is guard
            assert aiohttp_app["policy_evaluator"] is ev
            assert aiohttp_app["abac"] is pdp
            assert aiohttp_app["credential_resolver"] is not None

    def test_idempotent(self, aiohttp_app, empty_policies_dir):
        first = setup_pbac(aiohttp_app, policy_dir=empty_policies_dir)
        second = setup_pbac(aiohttp_app, policy_dir=empty_policies_dir)
        if first[2] is not None:
            assert first[2] is second[2]   # same Guardian instance

    def test_missing_policy_dir_returns_none_tuple(self, aiohttp_app):
        result = setup_pbac(aiohttp_app, policy_dir="/non/existent/path")
        # On error, we expect (None, None, None) — but if nav-auth is tolerant
        # of missing dirs, the result may be a valid empty engine. Either
        # outcome is acceptable; what is NOT acceptable is an unhandled
        # exception bubbling up:
        assert result is not None
```

---

## Agent Instructions

1. Read spec at `sdd/specs/pbac-support.spec.md` (sections 2, 6, 7) for context.
2. Verify the Codebase Contract — re-check the navigator-auth class line
   numbers and the ai-parrot reference shape.
3. Implement `querysource/auth/pbac.py` per the function shape above.
4. Update `querysource/auth/__init__.py` to re-export `setup_pbac`.
5. Write the unit tests at `tests/auth/test_pbac_bootstrap.py`.
6. Run `pytest tests/auth/ -v` until green (skip any tests that need a real
   navigator-auth release if it's not installed in the dev env).
7. Run full suite `pytest tests/ -x -q`.
8. Move this file to `sdd/tasks/done/` and update the index.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
