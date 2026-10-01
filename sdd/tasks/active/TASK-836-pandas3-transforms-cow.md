# TASK-836: transforms.py: corregir Copy-on-Write y astype sin asignar

**Feature**: FEAT-161 — Migración a pandas >= 3.0 (modin opcional)
**Spec**: `sdd/specs/pandas3-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S
**Depends-on**: TASK-834
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 3 (parte transforms). Bajo CoW, `df[field].fillna(..., inplace=True)` no modifica `df`.

---

## Scope

- `to_json` (`transforms.py:1358`): `df[field].fillna("[]", inplace=True)` → `df[field] = df[field].fillna("[]")`.
- `string_to_date` (`:1557`) y `epoch_to_date` (`:1573`): la línea `df[field].astype("datetime64[ns]")` (1569, 1599) descarta su resultado. Elimínala (preserva el comportamiento actual) — *why*: asignarla convertiría `None`→`NaT` y cambiaría la salida que hoy reciben los consumidores.
- Tests de las tres funciones bajo pandas 3.

**NOT in scope**: los otros `astype("datetime64[ns]")` asignados (32, 1426, 1483, 1550) — son explícitos y válidos.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/types/dt/transforms.py` | MODIFY | to_json fillna, string_to_date/epoch_to_date astype |
| `tests/unit/test_pandas3_transforms.py` | CREATE | tests |

---

## Codebase Contract (Anti-Hallucination)

### Existing Signatures to Use
```python
# querysource/types/dt/transforms.py
def to_json(df: pd.DataFrame, field: str):                                   # line 1358
def string_to_date(df: pd.DataFrame, field: str, column="", format="%Y-%m-%d"):  # line 1557
def epoch_to_date(...):                                                      # line 1573
```
Import en tests: `from querysource.types.dt.transforms import to_json, string_to_date, epoch_to_date`.

### Does NOT Exist
- ~~`transforms.to_json` como método de clase~~ — son funciones de módulo.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/types/dt/transforms.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/unit/test_pandas3_transforms.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": []
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Test rojo para `to_json` con NaN — *why*: demuestra el no-op CoW.
2. Fix `to_json`.
3. Elimina las dos líneas `astype` muertas; tests de no-regresión.

### `querysource/types/dt/transforms.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '        df[field].fillna("[]", inplace=True)' transforms.py)
# REPLACE (verified: transforms.py:1368):
        df[field] = df[field].fillna("[]")
```
```python
# occurrences: 2 (verified: grep -c '    df[field].astype("datetime64\[ns\]")' transforms.py)
# FILL IN: disambiguate — delete the bare line at :1569 (preceded by
#   `    df[field] = df[field].replace({pd.NaT: None})`) and at :1599 (preceded by
#   `            logging.error(err)`); both are immediately followed by `    return df`.
```

### `tests/unit/test_pandas3_transforms.py` (CREATE)
```python
"""FEAT-161: transforms under pandas 3 CoW."""
import numpy as np
import pandas as pd
from querysource.types.dt.transforms import to_json, string_to_date, epoch_to_date


def test_transforms_fillna_cow():
    df = pd.DataFrame({"j": ["{'a': 1}", np.nan]})
    out = to_json(df, "j")
    assert out["j"].iloc[1] == []


def test_string_to_date_pandas3():
    # FILL IN: assert parsed values and NaT->None mapping unchanged
    ...


def test_epoch_to_date_pandas3():
    # FILL IN: same for epoch_to_date with its real signature
    ...
```

### FILL IN checklist
- [ ] Firma completa de `epoch_to_date` — léela en :1573
- [ ] Valores esperados de los tests de fechas — bounded by el comportamiento con pandas 2.2 (baseline)

---

## Acceptance Criteria

- [ ] `to_json` rellena NaN bajo pandas 3
- [ ] Sin `ChainedAssignmentError` al ejecutar los tests con `-W error`
- [ ] `ruff check querysource/types/dt/transforms.py` sin nuevos errores

---

## Validation Commands

- `pytest tests/unit/test_pandas3_transforms.py -q`

---

## Test Specification

ver blueprint

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug pandas3-migration --feature-id FEAT-161`), never on `dev`.
2. Read the spec `sdd/specs/pandas3-migration.spec.md`.
3. Check every `Depends-on` task is `done` in `sdd/tasks/index/pandas3-migration.json`.
4. Verify the Codebase Contract (re-run the `grep -c` anchors) before writing code.
5. Set status `in-progress` in the index; implement from the blueprint; complete every `FILL IN`.
6. Run the Validation Commands; commit only the listed files.
7. Close with `scripts/sdd/close_task.sh <TASK-ID> pandas3-migration verified`; fill the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
