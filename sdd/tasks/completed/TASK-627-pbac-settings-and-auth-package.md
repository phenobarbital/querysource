# TASK-627: PBAC settings and auth-package skeleton

**Feature**: pbac-support
**Spec**: `sdd/specs/pbac-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Foundational task for FEAT-091. Adds the three new settings keys to
`querysource/conf.py` and creates the empty `querysource/auth/` package that
subsequent tasks (PBAC bootstrap, credential resolver, AbstractHandler helpers)
will populate. Implements **Module 1** of the spec.

Without this task, no other PBAC code can land — every later module imports
either a setting from `conf.py` or a symbol from `querysource.auth`.

---

## Scope

- Add three settings to `querysource/conf.py` following the existing
  `config.get*` convention:
  - `QS_PBAC_ENABLED: bool` (default `False`)
  - `QS_POLICY_PATH: str` (default `str(BASE_DIR / 'policies')`)
  - `QS_PBAC_CACHE_TTL: int` (default `300`)
- Create the empty `querysource/auth/` package directory with an
  `__init__.py` that exposes nothing yet (placeholder docstring + module-level
  logger).
- Do NOT implement `setup_pbac` or `CredentialResolver` here — those are
  TASK-629 and TASK-628 respectively.
- Do NOT bump `pyproject.toml`'s `navigator-auth` pin in this task — that
  bump lands with TASK-640 once the upstream PR is merged.

**NOT in scope**: anything that imports `navigator-auth`, any handler edits,
any test scaffolding (covered by TASK-641).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/conf.py` | MODIFY | Add three `QS_PBAC_*` settings. |
| `querysource/auth/__init__.py` | CREATE | Empty package init with docstring + module logger. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
from navconfig import BASE_DIR, config   # querysource/conf.py:5 (already imported)
```

### Existing Signatures to Use

```python
# querysource/conf.py — existing settings convention to mirror:
CACHE_HOST = config.get('CACHE_HOST', fallback='localhost')          # line 78
ENABLE_QS_SCHEDULER = config.getboolean('ENABLE_QS_SCHEDULER', fallback=False)  # line 343
DEFAULT_QUERY_TIMEOUT = config.getint('DEFAULT_QUERY_TIMEOUT', fallback=3600)   # line 349

# querysource/conf.py — fallback import block (must remain at end of file):
try:
    from settings.settings import *   # line 405-407
except ImportError:
    pass

# BASE_DIR is a pathlib.Path — verified usage:
# BASE_DIR / 'policies'  → joins safely on all platforms.
```

### Does NOT Exist

- ~~`querysource/auth/`~~ — package does not exist. This task creates it.
- ~~`QS_PBAC_*` settings~~ — none of `QS_PBAC_ENABLED`, `QS_POLICY_PATH`,
  `QS_PBAC_CACHE_TTL` exist yet. This task adds them.
- ~~`querysource.auth.pbac.setup_pbac`~~ / ~~`querysource.auth.credentials.CredentialResolver`~~ —
  introduced by TASK-629 and TASK-628 respectively, NOT by this task.

---

## Implementation Notes

### Pattern to Follow

Place the new settings near the bottom of `conf.py` — before the
`from settings.settings import *` fallback block (line 405-407) but after
the existing `ENABLE_QS_*` group (lines 340–355) for thematic grouping.

```python
# PBAC (FEAT-091) — added by TASK-627
QS_PBAC_ENABLED   = config.getboolean('QS_PBAC_ENABLED', fallback=False)
QS_POLICY_PATH    = config.get('QS_POLICY_PATH', fallback=str(BASE_DIR / 'policies'))
QS_PBAC_CACHE_TTL = config.getint('QS_PBAC_CACHE_TTL', fallback=300)
```

### `querysource/auth/__init__.py`

Single short docstring, no symbol re-exports yet (subsequent tasks will edit
this file to add re-exports as their pieces land):

```python
"""
querysource.auth — Policy-based access control (PBAC) for QuerySource.

This package wires navigator-auth's PBAC engine into QuerySource handlers
and provides a per-user credential resolver for the driver layer.

Public surface (filled in by subsequent tasks):
- ``setup_pbac()``        — TASK-629
- ``CredentialResolver``  — TASK-628
"""
import logging

logger = logging.getLogger(__name__)
```

### Key Constraints

- Settings must follow `config.getboolean` / `config.get` / `config.getint`
  conventions exactly — do NOT use `os.getenv`.
- `QS_POLICY_PATH` default must be `str(BASE_DIR / 'policies')` (string,
  not Path) so it round-trips through env-var overrides cleanly.
- The package init must NOT import `navigator-auth` — keep it lazy until
  the bootstrap task wires it.

### References in Codebase

- `querysource/conf.py:38-44` — `PG_*` settings, structurally identical
  pattern.
- `querysource/conf.py:343` — `getboolean` example.
- `querysource/conf.py:349` — `getint` example.

---

## Acceptance Criteria

- [ ] `from querysource.conf import QS_PBAC_ENABLED, QS_POLICY_PATH, QS_PBAC_CACHE_TTL` succeeds.
- [ ] With no env vars set: `QS_PBAC_ENABLED is False`, `QS_PBAC_CACHE_TTL == 300`,
      `QS_POLICY_PATH` ends with `/policies`.
- [ ] Setting `QS_PBAC_ENABLED=True` env var produces `QS_PBAC_ENABLED is True`
      after re-import.
- [ ] `from querysource.auth import logger` succeeds.
- [ ] `querysource.auth.__init__` does NOT import `navigator-auth` (verify
      with `python -c "import querysource.auth; import sys;
      assert 'navigator_auth' not in sys.modules"`).
- [ ] No regressions: full existing `pytest` suite still passes.

---

## Test Specification

No dedicated tests for this task — it's pure scaffolding. Verification is
the acceptance-criteria import / value checks. The unit test for settings
loading lands as part of TASK-641.

```bash
# Smoke check the agent should run before marking complete:
python -c "
from querysource.conf import QS_PBAC_ENABLED, QS_POLICY_PATH, QS_PBAC_CACHE_TTL
assert QS_PBAC_ENABLED is False
assert QS_PBAC_CACHE_TTL == 300
assert QS_POLICY_PATH.endswith('/policies')
import querysource.auth
import sys
assert 'navigator_auth' not in sys.modules, 'auth package must not eager-import navigator-auth'
print('OK')
"
```

---

## Agent Instructions

1. Read the spec at `sdd/specs/pbac-support.spec.md` (sections 2 + 6) for full context.
2. Verify the Codebase Contract — re-grep the listed line numbers in `conf.py`.
3. Implement following the scope and notes above.
4. Run the smoke check from the Test Specification section.
5. Run `pytest tests/ -x -q` (or the project's standard suite) to verify no regressions.
6. Move this file to `sdd/tasks/done/TASK-627-pbac-settings-and-auth-package.md`.
7. Update `sdd/tasks/.index.json` → `"status": "done"`.
8. Fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
