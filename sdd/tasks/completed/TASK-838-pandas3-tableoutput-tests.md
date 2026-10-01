# TASK-838: TableOutput: tests pandas 3 con driver mockeado

**Feature**: FEAT-161 — Migración a pandas >= 3.0 (modin opcional)
**Spec**: `sdd/specs/pandas3-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M
**Depends-on**: TASK-834
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 4. TableOutput es el principal consumidor de DataFrames hacia BD; el usuario pidió probarlo explícitamente.

---

## Scope

- Tests para `querysource/outputs/tables/TableOutput/table.py::TableOutput` (`table_output`, `run`) y `querysource/handlers/outputs/tableOutput/table.py::TableOutput`, con el engine/driver mockeado (`AsyncMock`), DataFrames con columnas `str`, NA, datetime `us`, jsonb.
- Verifica que el driver recibe los datos esperados (filas, None vs NaN, tipos).
- Si un test revela un bug de pandas 3 en TableOutput, NO lo corrijas aquí: regístralo en la Completion Note para TASK-839 — *why*: esta task sólo declara el archivo de test.

**NOT in scope**: modificar TableOutput; tests con BD real.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/unit/test_pandas3_tableoutput.py` | CREATE | tests de ambos TableOutput |

---

## Codebase Contract (Anti-Hallucination)

### Existing Signatures to Use
```python
# querysource/outputs/tables/TableOutput/table.py
class TableOutput:                                                     # line 19
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None  # line 20 (jsonb_columns, flavor, truncate kwargs)
    async def table_output(self, elem, datasource: pd.DataFrame)       # line 59 (uses self._engine.is_external, elem.tablename, elem.schema)
    async def run(self)                                                # line 185
# querysource/handlers/outputs/tableOutput/table.py
class TableOutput:                                                     # L12-137; run L101-137
```
Lee completos ambos archivos y `querysource/outputs/tables/TableOutput/postgres.py` (PgOutput) antes de diseñar los mocks.

### Does NOT Exist
- ~~fixture de BD en tests/unit~~ — mockear, no conectar.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "tests/unit/test_pandas3_tableoutput.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/outputs/tables/TableOutput/table.py#TableOutput",
    "sym:querysource/handlers/outputs/tableOutput/table.py#TableOutput"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Lee `table_output`/`run` completos para saber qué llama sobre `self._engine` — *why*: el mock debe imitar esa interfaz exacta.
2. Escribe los tests.

### `tests/unit/test_pandas3_tableoutput.py` (CREATE)
```python
"""FEAT-161: TableOutput with pandas 3 frames."""
from unittest.mock import AsyncMock, MagicMock
import pandas as pd
import pytest
from querysource.outputs.tables.TableOutput.table import TableOutput


@pytest.fixture
def df_pandas3():
    return pd.DataFrame({
        "name": pd.Series(["a", None, "c"], dtype="str"),
        "n": [1, None, 3],
        "ts": pd.to_datetime(["2026-01-01", None, "2026-01-03"]).as_unit("us"),
    })


async def test_tableoutput_str_na_datetime(df_pandas3):
    # FILL IN: build elem (tablename/schema), mock engine per table_output's calls,
    #          await table_output/run, assert rows sent to driver (None for NA, datetimes)
    ...


async def test_handler_tableoutput_pandas3(df_pandas3):
    # FILL IN: same for querysource.handlers.outputs.tableOutput.table.TableOutput
    ...
```

### FILL IN checklist
- [ ] Interfaz exacta del engine mockeado — bounded by el código leído en el paso 1
- [ ] Aserciones de NA→None — bounded by el comportamiento con pandas 2.2

---

## Acceptance Criteria

- [ ] Tests de ambos TableOutput pasan bajo pandas 3 (o fallos documentados para TASK-839)

---

## Validation Commands

- `pytest tests/unit/test_pandas3_tableoutput.py -q`

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

**Completed by**: sdd-worker orchestration
**Date**: 2026-10-02
**Notes**: tests/unit/test_pandas3_tableoutput.py: 7 tests, no pandas 3 bugs found in either TableOutput; 2 handler tests skip without built extensions, all pass in the main checkout.

**Deviations from spec**: none
