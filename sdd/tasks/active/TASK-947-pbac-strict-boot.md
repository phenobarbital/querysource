# TASK-947: QS_PBAC_STRICT_BOOT — abort startup when PBAC bootstrap fails

**Feature**: FEAT-182 — PBAC Coverage for the Management API, Direct Driver Route and Describe Fallback
**Spec**: `sdd/specs/querysource-management-pbac.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-946
**Assigned-to**: unassigned

---

## Context

Spec §1 (fifth issue), §3 Module 4. With `QS_PBAC_ENABLED=True`, a failed `setup_pbac()` (for example,
bad YAML) only logs a warning (`services.py:159-163`), and QS keeps running with enforcement off:
a silent fail-open. The new flag makes that failure fatal. It defaults to off.

---

## Scope

- Add `QS_PBAC_STRICT_BOOT = config.getboolean('QS_PBAC_STRICT_BOOT', fallback=False)` to `conf.py` next to the `QS_PBAC_*` block.
- In `QuerySource.setup()`: when PBAC is enabled, the bootstrap returned no guardian and the flag is
  true, raise `RuntimeError` with the same diagnostic text. Otherwise keep the warning.
- Write tests.

**NOT in scope**: changing `setup_pbac()` itself.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/conf.py` | MODIFY | new flag |
| `querysource/services.py` | MODIFY | raise when strict |
| `tests/auth/test_pbac_strict_boot.py` | CREATE | tests |

---

## Codebase Contract (Anti-Hallucination)

### Existing Signatures to Use
```python
# querysource/conf.py
QS_PBAC_ENABLED = config.getboolean('QS_PBAC_ENABLED', fallback=False)            # :463
QS_POLICY_PATH = config.get('QS_POLICY_PATH', fallback=str(BASE_DIR / 'policies'))  # :464
QS_PBAC_CACHE_TTL = config.getint('QS_PBAC_CACHE_TTL', fallback=300)               # :465

# querysource/services.py (inside QuerySource.setup)
#   imports QS_PBAC_CACHE_TTL, QS_PBAC_ENABLED from .conf (:45-46)
#   if QS_PBAC_ENABLED: _pdp, _evaluator, _guardian = setup_pbac(...)   (:147-153)
#   if _guardian is not None: info log  else: _svc_logger.warning("QS_PBAC_ENABLED=True but PBAC bootstrap returned None. " ...)  (:154-163)
```

### Does NOT Exist
- ~~`QS_PBAC_STRICT_BOOT`~~: new.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/conf.py", "action": "MODIFY"},
    {"path": "querysource/services.py", "action": "MODIFY"},
    {"path": "tests/auth/test_pbac_strict_boot.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/services.py#QuerySource.setup",
    "sym:querysource/auth/pbac.py#setup_pbac"
  ]
}
```

---

## Implementation Notes

- `conf.py` is also edited by TASK-946 (hence the dependency). Re-locate anchors after it lands.
- Tests: patch `querysource.services.setup_pbac` to return `(None, None, None)` and patch the module flags.
  Do not build a whole `QuerySource` if a narrower seam exists. FILL IN: pick the narrowest way to drive
  `setup()`'s PBAC block, bounded by "no network/DB".

---

## Implementation Blueprint

### Steps (in order)
1. Add the flag after `QS_PBAC_CACHE_TTL` in `conf.py`.
2. Import it in `services.py` with the other `QS_PBAC_*` names.
3. In the `else:` warning branch, raise when strict.
4. Write the tests.

### `querysource/conf.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "QS_PBAC_CACHE_TTL = config.getint" querysource/conf.py)
# AFTER — insert below `QS_PBAC_CACHE_TTL = config.getint('QS_PBAC_CACHE_TTL', fallback=300)` (verified: conf.py:465)
# FEAT-182: abort startup when QS_PBAC_ENABLED is on but the PBAC bootstrap failed,
# instead of silently running with enforcement off.
QS_PBAC_STRICT_BOOT = config.getboolean('QS_PBAC_STRICT_BOOT', fallback=False)
```

### `querysource/services.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "PBAC bootstrap returned None" querysource/services.py)
# REPLACE the else-branch warning (verified: services.py:159-163) with:
            else:
                _msg = (
                    "QS_PBAC_ENABLED=True but PBAC bootstrap returned None. "
                    "Policy enforcement is OFF for this process. "
                    "Check QS_POLICY_PATH and navigator-auth installation."
                )
                if QS_PBAC_STRICT_BOOT:
                    raise RuntimeError(_msg)
                _svc_logger.warning(_msg)
# FILL IN: add QS_PBAC_STRICT_BOOT to the `from .conf import (...)` list (:44-47).
```

### `tests/auth/test_pbac_strict_boot.py` (CREATE)
```python
"""FEAT-182 TASK-947: QS_PBAC_STRICT_BOOT."""
import pytest


def test_strict_boot_raises(monkeypatch):
    """Enabled + strict + bootstrap None → RuntimeError."""
    # FILL IN


def test_non_strict_boot_warns(monkeypatch, caplog):
    """Enabled + not strict + bootstrap None → warning only (today's behaviour)."""
    # FILL IN
```

### FILL IN checklist
- [ ] Import list in `services.py`.
- [ ] Narrowest seam for the tests.

---

## Acceptance Criteria

- [ ] Strict + failed bootstrap → `RuntimeError` from `setup()`.
- [ ] Default → warning only, as today.

---

## Validation Commands

- `pytest tests/auth/test_pbac_strict_boot.py -q`
- `pytest tests/auth/test_pbac_bootstrap.py -q`

---

## Test Specification

See the blueprint above.

---

## Agent Instructions

Same as TASK-943, with this task's id and files. TASK-946 must be `done` first.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
