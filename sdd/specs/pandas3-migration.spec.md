---
type: feature
base_branch: dev
projects: [querysource, outputs, multiquery]
tags: [pandas, pandas3, modin, dependencies, copy-on-write]
---

# Feature Specification: Migración a pandas >= 3.0 (modin opcional)

**Feature ID**: FEAT-161
**Date**: 2026-10-02
**Author**: Jesus Lara (jlara@trocglobal.com)
**Status**: approved
**Target version**: 5.2.0

---

## 1. Motivation & Business Requirements

### Problem Statement

QuerySource corre hoy con pandas 2.2.3, que llega de forma transitiva (no hay
`pandas` declarado en `pyproject.toml`). El ancla real que impide subir es
`modin==0.32.0` (`pandas>=2.2,<2.3`); incluso la última modin publicada
(0.37.1) exige `pandas<2.4`, así que **no existe modin compatible con
pandas 3**. pandas 3 trae cambios de comportamiento obligatorios —
Copy-on-Write (CoW), dtype `str` por defecto para texto, resolución de
datetime no forzada a `ns`, alias de frecuencia retirados — que pueden romper
en silencio el código de outputs (TableOutput) y los operadores/transformaciones
de MultiQS.

### Goals
- Declarar `pandas>=3.0,<4` como dependencia base explícita.
- Hacer modin **opcional** (extra `[modin]`), con fallback a pandas en el
  backend `'modin'` de los operadores MultiQS y error claro en `modinFormat`.
- Corregir las roturas de pandas 3 detectadas por tests (CoW, string dtype,
  datetime resolution, freq aliases).
- Añadir tests específicos de pandas 3 para TableOutput, operadores MultiQS y
  el fallback de modin.

### Non-Goals (explicitly out of scope)
- Compatibilidad dual pandas 2.2 + 3 (rechazado: piso `pandas>=3.0`).
- Eliminar modin del todo o depender de modin desde git (rechazados).
- Migrar código pandas a polars.
- Refactors no relacionados con las roturas de pandas 3.

---

## 2. Architectural Design

### Overview

Enfoque **empírico en worktree**:
1. Baseline: ejecutar la suite actual con pandas 2.2 y guardar el resultado
   (`sdd/state/FEAT-161/baseline.txt`, lista de tests fallidos pre-existentes).
2. Cambiar dependencias (`pandas>=3.0,<4`; modin → extra) y `uv lock`/sync.
   Los extras de `analytics` que no resuelvan con pandas 3 se ajustan de
   versión o se aíslan en su propio extra; cada caso se reporta.
3. Modin opcional: import protegido + `logger.warning` + fallback a pandas.
4. Re-ejecutar la suite con pandas 3; el diff contra baseline guía las
   correcciones (TDD: test rojo → fix → verde).
5. Barrido dirigido de patrones conocidos (ver §6 Edit Sites), aunque ningún
   test falle, porque varios son no-ops silenciosos bajo CoW.

### Component Diagram
```
pyproject.toml ──→ uv.lock (pandas 3.x, modin fuera de base)
       │
AbstractOperator(backend='modin') ──try import modin──→ fallback pd + warning
modinFormat ──ImportError──→ QueryException("instale querysource[modin]")
       │
types/dt/transforms.py, filters.py ─┐
multi/operators/*, transformations/*├─→ fixes CoW / str dtype / datetime
outputs/tables/TableOutput/*  ──────┘
```

### Integration Points
| Existing Component | Integration Type | Notes |
|---|---|---|
| `AbstractOperator.__init__` | modifies | fallback modin → pandas |
| `modinFormat.__init__` | modifies | error explícito si falta modin |
| `TableOutput` | tests | DataFrames str/NA/datetime con driver mockeado |
| `types/dt/transforms.py` | modifies | CoW + datetime64 |
| `multi/operators/filter/flt.py` | modifies | `select_dtypes` datetime con cualquier resolución |

### Data Models
Sin modelos nuevos.

### New Public Interfaces
Solo el extra de instalación `querysource[modin]`. Sin API Python nueva.

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: Dependencias | no | — | resolución de extras analytics requiere juicio |
| M2: Modin opcional | yes | try/except ImportError, warning, fallback `pd`; `QueryException` en modinFormat | — |
| M3: Fixes pandas 3 | no | — | guiado por fallos reales |
| M4: Tests pandas 3 | yes | archivos y casos fijados en §4 | — |

### Module 1: Dependencias
- **Path**: `pyproject.toml`, `uv.lock`
- **Responsibility**: `pandas>=3.0,<4` en base; quitar `modin==0.32.0` de base
  (`pyproject.toml:110`); añadir `[project.optional-dependencies] modin =
  ["modin>=0.37.1"]` con comentario de que hoy no coinstala con pandas 3;
  ajustar/aislar extras analytics incompatibles; `uv lock`.
- **Depends on**: — (exclusivo: lockfile)

### Module 2: Modin opcional
- **Path**: `querysource/queries/multi/operators/abstract.py`, `querysource/outputs/dt/modin.py`
- **Depends on**: M1
- **Interface Skeleton**:
  ```python
  # modifies querysource/queries/multi/operators/abstract.py:26
  def __init__(self, data: dict, **kwargs) -> None:
      """backend='modin': intenta `import modin.pandas`; si ImportError,
      registra warning y usa pandas (self._backend = 'pandas')."""
  # modifies querysource/outputs/dt/modin.py:8
  class modinFormat(OutputFormat):
      def __init__(self) -> None:
          """Raises QueryException indicando `pip install querysource[modin]`
          si modin/distributed no están instalados."""
  ```

### Module 3: Fixes pandas 3
- **Path**: ver §6 Edit Sites
- **Depends on**: M1
- **Responsibility**:
  - CoW: reemplazar `df[col].method(..., inplace=True)` por reasignación
    (`transforms.py:1368`); revisar `astype` sin asignar (`transforms.py:1569, 1599`
    — hoy son no-ops; asignar o eliminar preservando el comportamiento actual).
  - String dtype: `FilterCols.py:83` `col.dtype == object` →
    `pd.api.types.is_string_dtype(col) or col.dtype == object`.
  - Datetime: `flt.py:102` `select_dtypes(include=["datetime64[ns]"])` →
    `include=["datetime", "datetimetz"]`.
  - `inplace=True` sobre DataFrames propios se mantiene (válido en pandas 3);
    solo se corrigen los aplicados a una Series extraída.
  - Recompilar `.pyx` sólo si alguno se modifica.

### Module 4: Tests pandas 3
- **Path**: `tests/unit/test_pandas3_tableoutput.py`, `tests/unit/test_pandas3_operators.py`,
  `tests/unit/test_pandas3_modin_fallback.py`, `tests/unit/test_pandas3_transforms.py`
- **Depends on**: M2 (fallback), M3 (fixes)

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_pandas_version_is_3` | M1 | `pd.__version__ >= 3` |
| `test_operator_modin_fallback` | M2 | backend='modin' sin modin → `_pd is pandas` + warning |
| `test_modinformat_missing_raises` | M2 | modinFormat sin modin → error con "querysource[modin]" |
| `test_tableoutput_str_na_datetime` | M4 | TableOutput con columnas `str`, NA, datetime `us`; driver mock recibe filas correctas |
| `test_join/concat/melt/groupby/pivot/filter_pandas3` | M4 | resultados correctos con dtype `str` y NA |
| `test_flt_datetime_any_resolution` | M3 | filtro sobre columnas datetime `us`/`ms` |
| `test_filtercols_empty_str_column` | M3 | columna `str` toda vacía se elimina |
| `test_transforms_fillna_cow` | M3 | `transforms.py` función de 1368 rellena NaN bajo CoW |

### Integration Tests
| Test | Description |
|---|---|
| suite completa | `pytest tests/` con pandas 3 ≥ baseline (no nuevos fallos) |

### Test Data / Fixtures
```python
@pytest.fixture
def df_pandas3():
    return pd.DataFrame({"name": ["a", None, ""], "n": [1, None, 3],
                         "ts": pd.to_datetime(["2026-01-01", None, "2026-01-03"]).as_unit("us")})
```

---

## 5. Acceptance Criteria

- [ ] `pyproject.toml` declara `pandas>=3.0,<4`; `uv lock` resuelve; entorno con pandas 3.x.
- [ ] modin no está en dependencias base; existe extra `[modin]` documentado.
- [ ] `import querysource` y los operadores MultiQS funcionan sin modin instalado.
- [ ] backend `'modin'` sin modin → warning + pandas; `modinFormat` → error claro.
- [ ] Todos los tests nuevos `tests/unit/test_pandas3_*.py` pasan.
- [ ] `pytest tests/` no introduce fallos respecto al baseline pandas 2.2.
- [ ] `ruff check` limpio en archivos tocados.
- [ ] Extras analytics: cada paquete incompatible reportado y ajustado/aislado.

---

## 6. Codebase Contract

### Verified Imports
```python
import pandas as pd                                   # querysource/queries/multi/operators/abstract.py:9
from ....exceptions import QueryException             # querysource/queries/multi/operators/abstract.py:12
from .abstract import OutputFormat                    # querysource/outputs/dt/modin.py:1
from ...conf import MODIN_SERVER                      # querysource/outputs/dt/modin.py:11 ; conf.py:439
from querysource.outputs.tables.TableOutput.table import TableOutput  # table.py:19
```

### Existing Class Signatures
```python
# querysource/queries/multi/operators/abstract.py
class AbstractOperator(AbstractMulti):          # line 16
    def __init__(self, data: dict, **kwargs) -> None:  # line 26; self._backend, self._pd
# querysource/outputs/dt/modin.py
class modinFormat(OutputFormat):                # line 4
    async def serialize(self, result, error, *args, **kwargs)  # line 22
# querysource/outputs/tables/TableOutput/table.py
class TableOutput:                              # line 19
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None  # line 20
    async def table_output(self, elem, datasource: pd.DataFrame)  # line 59
    async def run(self)                         # line 185
```

### Does NOT Exist (Anti-Hallucination)
- ~~modin con soporte pandas 3~~ — modin 0.37.1 (última en PyPI) exige `pandas<2.4`.
- ~~`pandas` declarado en `pyproject.toml`~~ — hoy es sólo transitivo.
- ~~extra `[modin]`~~ — se crea en M1.
- Operadores existentes: Concat, GroupBy, Info, Join, Melt, Merge, filter/ (no hay Pivot operator; Pivot es transformación `multi/transformations/pivot.py`).

### Edit Sites (Blueprint Anchors)

Verified against: `0e325bd`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `pyproject.toml` | MODIFY | `"modin==0.32.0",` | `pyproject.toml:110` | 1 |
| `pyproject.toml` | MODIFY | `analytics = [` | `pyproject.toml:123` | 1 |
| `querysource/queries/multi/operators/abstract.py` | MODIFY | `            import modin.pandas as mpd` | `abstract.py:30` | 1 |
| `querysource/outputs/dt/modin.py` | MODIFY | `        import modin.config as modin_cfg` | `modin.py:9` | 1 |
| `querysource/types/dt/transforms.py` | MODIFY | `        df[field].fillna("[]", inplace=True)` | `transforms.py:1368` | 1 |
| `querysource/types/dt/transforms.py` | MODIFY | `    df[field].astype("datetime64[ns]")` (+ línea previa `df[field] = df[field].replace({pd.NaT: None})`) | `transforms.py:1569` | 2 (1569, 1599) |
| `querysource/queries/multi/transformations/FilterCols.py` | MODIFY | `                if col.dtype == object:` | `FilterCols.py:83` | 1 |
| `querysource/queries/multi/operators/filter/flt.py` | MODIFY | `            u = self.data.select_dtypes(include=["datetime64[ns]"])` | `flt.py:102` | 1 |
| `tests/unit/test_pandas3_tableoutput.py` | CREATE | — | — | — |
| `tests/unit/test_pandas3_operators.py` | CREATE | — | — | — |
| `tests/unit/test_pandas3_modin_fallback.py` | CREATE | — | — | — |
| `tests/unit/test_pandas3_transforms.py` | CREATE | — | — | — |

Sitios adicionales que aparezcan al correr la suite bajo pandas 3 se añaden a la task de M3.

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- `self.logger` / `logging.getLogger(__name__)`, nunca `print`.
- `uv add` / `uv lock`; dependencias en `pyproject.toml`.
- TDD: test rojo bajo pandas 3 antes de cada fix.

### Known Risks / Gotchas
- **CoW silencioso**: `df[col].fillna(..., inplace=True)` no falla, sólo no
  modifica `df` (pandas 3 emite `ChainedAssignmentError` warning). Ejecutar
  tests con `-W error::FutureWarning -W error::pandas.errors.ChainedAssignmentError`
  en el barrido.
- **dtype `str`**: `dtype == object` deja de ser True para texto; `select_dtypes(include=["object","string"])` ya cubre ambos.
- **datetime**: `to_datetime` de strings puede devolver `us`; comparar contra `datetime64[ns]` literal falla.
- **Extras analytics** (pandas-bokeh 0.5.5, lux, dtale, sweetviz, pandas-eda, pygwalker) pueden fijar pandas<3 → aislar.
- **asyncdb** declara `pandas>=2.2.3`: compatible; sus outputs pandas también deben probarse vía TableOutput.
- Usuarios de backend `'modin'` pierden paralelismo silenciosamente (mitigado por warning).

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `pandas` | `>=3.0,<4` | objetivo |
| `modin` | `>=0.37.1` (extra) | opcional; hoy incompatible con pandas 3 |
| `numpy` | `>=2.0,<2.4` (revisar con lock) | pandas 3 requiere `>=1.26` |

---

## 8. Open Questions

- [x] ¿Qué hacer con modin? — *Resolved (usuario, 2026-10-02)*: hacerlo opcional (extra) y subir pandas a 3.0.
- [x] ¿Enfoque? — *Resolved (usuario)*: empírico en worktree, con piso `pandas>=3.0` (sin compatibilidad dual).
- [x] Target version — *Resolved (usuario, 2026-10-02)*: 5.2.0.
- [x] Extras analytics incompatibles — *Resolved (usuario, 2026-10-02)*: aislarlos en un extra propio.

---

## Worktree Strategy

- Isolation: un worktree de feature (`.worktrees/pandas3`, rama `feat/pandas3-migration`).
- Grafo: M2 → M1, M3 → M1, M4 → M2 + M3.
- Shared files: `querysource/types/dt/transforms.py` (sólo M3).
- Exclusive resources: M1 (`uv.lock`, entorno) → `parallel: false`.
- Cross-feature dependencies: ninguna.

---

## 9. Design Research Cross-Check

> Status: skipped (sin documento de exploración aceptado; diseño acordado en sesión).

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-02 | Jesus Lara | Initial draft |
