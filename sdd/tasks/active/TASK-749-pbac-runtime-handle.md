# TASK-749: Process-wide PBAC runtime handle recorded by setup_pbac()

**Feature**: FEAT-150 — PBAC for Request-less (Programmatic) QS Callers
**Spec**: `sdd/specs/pbac-request-credentials.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Implements spec §3 **Module 2** (resolved Round 2: "evaluator comes from QuerySource's
own singleton state"). Today the guardian and evaluator live only on
`app['security']` / `app['policy_evaluator']`, so a request-less caller cannot reach
them. This task makes `setup_pbac()` record them in a module-level handle.
`QuerySource().app` is deliberately **not** used: calling `QuerySource()` in a process
that never set it up would construct the heavy singleton (spec §2 item 3).

---

## Scope

- Add the `PBACRuntime` frozen dataclass and `get_pbac_runtime()`, `clear_pbac_runtime()` and `_set_pbac_runtime()` to `querysource/auth/pbac.py`.
- Call `_set_pbac_runtime(guardian, evaluator)` in **both** success branches of `setup_pbac()`: the reuse branch and the fresh-bootstrap branch.
- Never set it on the three failure paths that `return (None, None, None)`.
- Write `tests/auth/test_pbac_runtime.py`.

**NOT in scope**: exporting `get_pbac_runtime` from `querysource/auth/__init__.py` (TASK-750 owns that file); any consumer of the handle.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/auth/pbac.py` | MODIFY | runtime handle + registration in both success branches |
| `tests/auth/test_pbac_runtime.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.auth.pbac import setup_pbac          # verified: querysource/auth/pbac.py:28
from querysource.auth.credentials import CredentialResolver   # verified: querysource/auth/pbac.py:20
```

### Existing Signatures to Use
```python
# querysource/auth/pbac.py:28
def setup_pbac(app: web.Application, policy_dir: str = "policies", cache_ttl: int = 300)
    -> "tuple[Optional[PDP], Optional[PolicyEvaluator], Optional[Guardian]]"
# :13  from __future__ import annotations
# :15  import logging
# :16  from typing import Optional, TYPE_CHECKING
# :61-68 reuse branch:
#     existing_guardian = app.get("security")
#     existing_evaluator = app.get("policy_evaluator")
#     if existing_guardian is not None and existing_evaluator is not None:
#         ...
#         return (app.get("abac"), existing_evaluator, existing_guardian)
# :136-140 registration (fresh bootstrap):
#     app["security"] = guardian
#     app["abac"] = pdp
#     app["policy_evaluator"] = evaluator
#     app["credential_resolver"] = CredentialResolver(logger=_log)
# failure paths return (None, None, None) — navigator-auth import error, policy load error, PDP build error
```

Existing test to keep green: `tests/auth/test_pbac_bootstrap.py` (read it for its app/fixture pattern).

### Does NOT Exist
- ~~`get_pbac_runtime` / `PBACRuntime` / any module-level evaluator accessor~~: created here.
- ~~`QuerySource().app` as a safe lookup~~: do not use it (spec §2 item 3).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/auth/pbac.py", "action": "MODIFY"},
    {"path": "tests/auth/test_pbac_runtime.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/auth/pbac.py#setup_pbac"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Keep all navigator-auth imports lazy. `pbac.py` must stay importable with `QS_PBAC_ENABLED=False` (module docstring :6-9).
- Use a plain module global plus accessor functions. No lock is needed, because assignment of one tuple-like object is atomic.
- The last successful `setup_pbac()` wins. Tests reset the handle with `clear_pbac_runtime()`.

---

## Implementation Blueprint

### Steps (in order)
1. Add `dataclass`/`Any` imports and the runtime block below the `TYPE_CHECKING` imports — *why*: the handle must exist before `setup_pbac` references it.
2. In the reuse branch, call `_set_pbac_runtime(existing_guardian, existing_evaluator)` just before its `return` — *why*: a parent navigator-api stack that ran `PDP.setup` first is still a valid PBAC runtime (spec §2 item 3).
3. After `app["credential_resolver"] = ...` in the registration block, call `_set_pbac_runtime(guardian, evaluator)` — *why*: register only once the app is fully wired.
4. Write the tests.

### `querysource/auth/pbac.py` (MODIFY) — imports and runtime block
```python
# occurrences: 1 (verified: grep -c 'from typing import Optional, TYPE_CHECKING' querysource/auth/pbac.py)
# REPLACE `from typing import Optional, TYPE_CHECKING` (verified: querysource/auth/pbac.py:16) with:
from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

# occurrences: 1 (verified: grep -c 'def setup_pbac(' querysource/auth/pbac.py)
# BEFORE — insert above `def setup_pbac(` (verified: querysource/auth/pbac.py:28):


@dataclass(frozen=True)
class PBACRuntime:
    """Guardian + evaluator registered by the last successful setup_pbac()."""

    guardian: Any
    evaluator: Any


_RUNTIME: PBACRuntime | None = None


def get_pbac_runtime() -> PBACRuntime | None:
    """Return the registered runtime, or None when setup_pbac() never succeeded in this process."""
    return _RUNTIME


def clear_pbac_runtime() -> None:
    """Forget the registered runtime (tests / app shutdown)."""
    global _RUNTIME
    _RUNTIME = None


def _set_pbac_runtime(guardian: Any, evaluator: Any) -> None:
    """Record the runtime; called only from setup_pbac() success branches."""
    global _RUNTIME
    _RUNTIME = PBACRuntime(guardian=guardian, evaluator=evaluator)
```
**Why this shape**: The names and signatures are fixed by spec §3 M2 and consumed verbatim by TASK-750.

### `querysource/auth/pbac.py` (MODIFY) — reuse branch
```python
# occurrences: 1 (verified: grep -c 'return (app.get("abac"), existing_evaluator, existing_guardian)' querysource/auth/pbac.py)
# BEFORE — insert immediately above `        return (app.get("abac"), existing_evaluator, existing_guardian)` (verified: querysource/auth/pbac.py:68)
        _set_pbac_runtime(existing_guardian, existing_evaluator)
```

### `querysource/auth/pbac.py` (MODIFY) — bootstrap registration
```python
# occurrences: 2 for `app["credential_resolver"] = CredentialResolver(logger=_log)` (verified: grep -c) — ambiguous.
# Disambiguated anchor (verified: querysource/auth/pbac.py:139-140), the unindented-branch pair:
#     app["policy_evaluator"] = evaluator
#     app["credential_resolver"] = CredentialResolver(logger=_log)
# AFTER — insert below that pair (NOT after the `if "credential_resolver" not in app:` one at :66-67):
    _set_pbac_runtime(guardian, evaluator)
```
**Why**: Registering on every success path, and only there, is what makes "no runtime" mean "PBAC not usable here". TASK-750 relies on that to decide between no-op and deny.

### FILL IN checklist
- [ ] Test bodies: bootstrap success records the runtime; reuse records the pre-existing pair; each failure path leaves it `None`; `clear_pbac_runtime()` resets it.

---

## Acceptance Criteria

- [ ] AC-1: after a successful fresh `setup_pbac(app, policy_dir=<tmp dir with one valid policy>)`, `get_pbac_runtime().evaluator is app["policy_evaluator"]` and `.guardian is app["security"]`.
- [ ] AC-2: when `app` already holds `security` and `policy_evaluator`, `setup_pbac(app)` records exactly those instances.
- [ ] AC-3: on each failure path (patch the navigator-auth import, the policy load or the PDP build to raise), `get_pbac_runtime()` stays `None`.
- [ ] AC-4: `clear_pbac_runtime()` resets it to `None`.
- [ ] AC-5: `tests/auth/test_pbac_bootstrap.py` passes unmodified.
- [ ] AC-6: `ruff check querysource/auth/pbac.py tests/auth/test_pbac_runtime.py` is clean.

---

## Validation Commands

- `pytest tests/auth/test_pbac_runtime.py -q`
- `pytest tests/auth/test_pbac_bootstrap.py -q`

---

## Test Specification

```python
# tests/auth/test_pbac_runtime.py
import pytest
from aiohttp import web

from querysource.auth.pbac import clear_pbac_runtime, get_pbac_runtime, setup_pbac


@pytest.fixture(autouse=True)
def _reset_runtime():
    clear_pbac_runtime()
    yield
    clear_pbac_runtime()


def test_bootstrap_records_runtime(tmp_path): ...     # AC-1 (write a minimal allow policy YAML into tmp_path)

def test_reuse_branch_records_existing(): ...        # AC-2 (MagicMock guardian/evaluator on web.Application())

def test_failure_leaves_runtime_none(monkeypatch, tmp_path): ...   # AC-3

def test_clear(): ...                                # AC-4
```

---

## Agent Instructions

1. Read spec §2 item 3 and §3 Module 2.
2. Re-run the `grep -c` checks above; stop and report if a count differs.
3. Implement from the blueprint, then run the Validation Commands and `ruff check`.
4. Move this file to `sdd/tasks/completed/`, set the index status to `done`, and fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
