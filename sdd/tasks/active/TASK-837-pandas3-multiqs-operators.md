# TASK-837: MultiQS: string dtype y datetime en FilterCols/Filter + tests de operadores

**Feature**: FEAT-161 — Migración a pandas >= 3.0 (modin opcional)
**Spec**: `sdd/specs/pandas3-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M
**Depends-on**: TASK-834
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 3/4 (MultiQS). Columna de texto vacía ya no es `object` → FilterCols(all_empty) no la detecta; `clean_dates` sólo ve `datetime64[ns]`.

---

## Scope

- `FilterCols._apply_filter` (`FilterCols.py:83`): `if col.dtype == object:` → `if col.dtype == object or pd.api.types.is_string_dtype(col):`.
- `Filter` (`flt.py:102`): `select_dtypes(include=["datetime64[ns]"])` → `select_dtypes(include=["datetime", "datetimetz"])`.
- Revisa `flt.py` bloque `drop_empty` (`self.data.dropna(how="all")` sin asignar, `self.data.is_copy = None`): bajo pandas 3 `is_copy` no existe; elimina esa asignación si emite warning/error — *why*: atributo retirado de pandas.
- Tests de operadores (Join, Concat, Melt, GroupBy, Merge, Filter) y transformaciones (FilterCols, pivot) con DataFrames `str`/NA/datetime `us`.

**NOT in scope**: `inplace=True` sobre DataFrames propios (válido en pandas 3); Forecast/correlation.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/transformations/FilterCols.py` | MODIFY | is_string_dtype |
| `querysource/queries/multi/operators/filter/flt.py` | MODIFY | select_dtypes datetime cualquier resolución |
| `tests/unit/test_pandas3_operators.py` | CREATE | tests Join/Concat/Melt/GroupBy/Merge/Filter + FilterCols + pivot |

---

## Codebase Contract (Anti-Hallucination)

### Existing Signatures to Use
```python
# querysource/queries/multi/transformations/FilterCols.py
class FilterCols(AbstractTransform):                                  # line 11
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs)      # line 51
    async def start(self) -> None                                      # line 68
    def _apply_filter(self, df: pd.DataFrame) -> pd.DataFrame          # line 73
    async def run(self) -> Union[dict, pd.DataFrame]                   # line 112
# querysource/queries/multi/operators/filter/flt.py
class Filter(AbstractOperator):                                       # line 12
    def __init__(self, data: dict, **kwargs) -> None                   # line 63
```
Operadores: `querysource/queries/multi/operators/{Concat,GroupBy,Join,Melt,Merge}.py`; pivot: `querysource/queries/multi/transformations/pivot.py`. Lee cada `__init__`/`start`/`run` antes de escribir su test; patrón de test existente: `tests/unit/test_multiqs_output_raise.py`.

### Does NOT Exist
- ~~operador `Pivot`~~ — pivot es transformación (`transformations/pivot.py`).
- ~~`DataFrame.is_copy`~~ en pandas 3.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/queries/multi/transformations/FilterCols.py",
      "action": "MODIFY"
    },
    {
      "path": "querysource/queries/multi/operators/filter/flt.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/unit/test_pandas3_operators.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/transformations/FilterCols.py#FilterCols",
    "sym:querysource/queries/multi/operators/filter/flt.py#Filter"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Tests rojos de FilterCols(all_empty) con columna `str` vacía y Filter(clean_dates) con datetime `us`.
2. Aplica los dos fixes.
3. Tests de humo de operadores con fixture común — *why*: detecta roturas pandas 3 no previstas; las que fallen se corrigen aquí si están en estos archivos, si no se reportan a TASK-839.

### `querysource/queries/multi/transformations/FilterCols.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '                if col.dtype == object:' FilterCols.py)
# REPLACE (verified: FilterCols.py:83):
                if col.dtype == object or pd.api.types.is_string_dtype(col):
```

### `querysource/queries/multi/operators/filter/flt.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'select_dtypes(include=\["datetime64\[ns\]"\])' flt.py)
# REPLACE (verified: flt.py:102):
            u = self.data.select_dtypes(include=["datetime", "datetimetz"])
```

### `tests/unit/test_pandas3_operators.py` (CREATE)
```python
"""FEAT-161: MultiQS operators/transformations under pandas 3."""
import pandas as pd
import pytest


@pytest.fixture
def df_pandas3():
    return pd.DataFrame({
        "name": ["a", None, ""], "n": [1, None, 3], "empty": ["", "", ""],
        "ts": pd.to_datetime(["2026-01-01", None, "2026-01-03"]).as_unit("us"),
    })


async def test_filtercols_empty_str_column(df_pandas3):
    # FILL IN: FilterCols(expression='all_empty') drops 'empty'
    ...


async def test_flt_datetime_any_resolution(df_pandas3):
    # FILL IN: Filter with clean_dates replaces NaT with None on 'ts'
    ...

# FILL IN: test_join/concat/melt/groupby/merge/pivot_pandas3 — one per operator
```

### FILL IN checklist
- [ ] Construcción real de cada operador (dict de datos con nombres, kwargs) — bounded by sus `__init__`
- [ ] Decisión sobre `is_copy`/`dropna` en `drop_empty` — bounded by: no cambiar semántica salvo eliminar el atributo retirado

---

## Acceptance Criteria

- [ ] FilterCols(all_empty) elimina columnas `str` vacías
- [ ] Filter(clean_dates) actúa sobre datetime `us`/`ms`
- [ ] Tests de los 6 operadores + pivot pasan bajo pandas 3

---

## Validation Commands

- `pytest tests/unit/test_pandas3_operators.py -q`

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
