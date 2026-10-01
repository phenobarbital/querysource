# TASK-835: Modin opcional: fallback a pandas y error claro en modinFormat

**Feature**: FEAT-161 — Migración a pandas >= 3.0 (modin opcional)
**Spec**: `sdd/specs/pandas3-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S
**Depends-on**: TASK-834
**Assigned-to**: unassigned

---

## Context

Implementa spec §3 Module 2. Tras TASK-834 modin ya no se instala; el backend `'modin'` no debe romper MultiQS.

---

## Scope

- En `AbstractOperator.__init__`, envuelve `import modin.pandas as mpd` en `try/except ImportError`: log warning y usa `pd`, poniendo `self._backend = 'pandas'`.
- En `modinFormat.__init__`, si `import modin.config` o `from distributed import Client` falla, lanza `QueryException` con mensaje que incluya `querysource[modin]`.
- Tests en `tests/unit/test_pandas3_modin_fallback.py`.

**NOT in scope**: soportar modin con pandas 3; cambiar `serialize`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/operators/abstract.py` | MODIFY | try/except ImportError + warning + fallback |
| `querysource/outputs/dt/modin.py` | MODIFY | QueryException si falta modin |
| `tests/unit/test_pandas3_modin_fallback.py` | CREATE | tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import pandas as pd                          # querysource/queries/multi/operators/abstract.py:9
from ....exceptions import QueryException    # querysource/queries/multi/operators/abstract.py:12
from .abstract import OutputFormat           # querysource/outputs/dt/modin.py:1
from querysource.exceptions import QueryException  # usar en modin.py (paquete querysource/exceptions)
```

### Existing Signatures to Use
```python
# querysource/queries/multi/operators/abstract.py
class AbstractOperator(AbstractMulti):              # line 16
    def __init__(self, data: dict, **kwargs) -> None:  # line 26
        self._backend = kwargs.get('backend', 'pandas')  # line 27
        if self._backend == 'modin':                  # line 29
            import modin.pandas as mpd                # line 30
# querysource/outputs/dt/modin.py
class modinFormat(OutputFormat):                    # line 4
    def __init__(self):                             # line 8
        import modin.config as modin_cfg            # line 9
        from distributed import Client              # line 10
```
`AbstractOperator` es abstracta (`start`, `run`): en tests usa una subclase mínima o `Concat` (`querysource/queries/multi/operators/Concat.py`).

### Does NOT Exist
- ~~`self.logger` antes de `super().__init__`~~ — verifica si `AbstractMulti.__init__` crea `self.logger`; si el warning va antes de `super()`, usa `logging.getLogger(__name__)`.
- ~~modin instalado en el entorno de test~~ — tras TASK-834 no lo está; aun así fuerza el ImportError con `monkeypatch.setitem(sys.modules, 'modin.pandas', None)`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/queries/multi/operators/abstract.py",
      "action": "MODIFY"
    },
    {
      "path": "querysource/outputs/dt/modin.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/unit/test_pandas3_modin_fallback.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/operators/abstract.py#AbstractOperator",
    "sym:querysource/outputs/dt/modin.py#modinFormat"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Escribe los tests (rojo) — *why*: TDD.
2. Modifica `abstract.py` — *why*: MultiQS no debe caer por un backend opcional.
3. Modifica `modin.py` — *why*: error accionable en vez de `ModuleNotFoundError`.

### `querysource/queries/multi/operators/abstract.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '            import modin.pandas as mpd' abstract.py)
# REPLACE lines 29-32 (verified: abstract.py:29-32) WITH:
        if self._backend == 'modin':
            try:
                import modin.pandas as mpd
                self._pd = mpd
            except ImportError:
                logging.getLogger(__name__).warning(
                    "Modin backend requested but modin is not installed "
                    "(pip install querysource[modin]); falling back to pandas."
                )
                self._backend = 'pandas'
                self._pd = pd
        else:
            self._pd = pd
```
Añade `import logging` arriba si no existe. **Why**: spec §2 — warning + fallback.

### `querysource/outputs/dt/modin.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '        import modin.config as modin_cfg' modin.py)
# REPLACE lines 9-10 (verified: modin.py:9-10) WITH:
        try:
            import modin.config as modin_cfg
            from distributed import Client
        except ImportError as exc:
            raise QueryException(
                "modinFormat requires modin: pip install querysource[modin]"
            ) from exc
```
**Why**: el output format explícito no puede degradarse en silencio — el usuario lo pidió.

### `tests/unit/test_pandas3_modin_fallback.py` (CREATE)
```python
"""FEAT-161: modin is optional."""
import sys
import pandas as pd
import pytest
from querysource.exceptions import QueryException


def test_operator_modin_fallback(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "modin.pandas", None)
    monkeypatch.setitem(sys.modules, "modin", None)
    # FILL IN: instantiate a concrete operator (e.g. Concat) with backend='modin'
    #          and assert op._pd is pd, op._backend == 'pandas', warning logged


def test_modinformat_missing_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "modin.config", None)
    monkeypatch.setitem(sys.modules, "modin", None)
    from querysource.outputs.dt.modin import modinFormat
    with pytest.raises(QueryException, match=r"querysource\[modin\]"):
        modinFormat()
```

### FILL IN checklist
- [ ] Constructor válido del operador concreto en el test — bounded by su `__init__` real
- [ ] Ruta exacta de `QueryException` en modin.py (`from ...exceptions import QueryException`) — verifica con grep

---

## Acceptance Criteria

- [ ] backend='modin' sin modin → `_pd is pandas` + warning
- [ ] `modinFormat()` sin modin → `QueryException` con `querysource[modin]`
- [ ] `ruff check` limpio en los dos módulos

---

## Validation Commands

- `pytest tests/unit/test_pandas3_modin_fallback.py -q`

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
**Notes**: Modin optional: abstract.py falls back to pandas with a warning; modinFormat raises QueryException naming querysource[modin]. Tests: tests/unit/test_pandas3_modin_fallback.py (pass with built extensions copied into the worktree).

**Deviations from spec**: none
