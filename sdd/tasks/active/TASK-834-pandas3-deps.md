# TASK-834: Dependencias: pandas>=3.0, modin a extra, extras analytics aislados

**Feature**: FEAT-161 — Migración a pandas >= 3.0 (modin opcional)
**Spec**: `sdd/specs/pandas3-migration.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Implementa spec §3 Module 1. Es la base de toda la feature: hasta que el entorno no tenga pandas 3 ninguna otra task puede ver sus tests en rojo.

---

## Scope

- **Primero** (antes de tocar dependencias) ejecuta `pytest tests/ -q -p no:cacheprovider -rf > sdd/state/FEAT-161/baseline.txt 2>&1` (o equivalente) con pandas 2.2 — *why*: AC de la spec exige comparar contra baseline.
- Añade `"pandas>=3.0,<4",` a `[project] dependencies`.
- Elimina `"modin==0.32.0",` de dependencias base.
- Añade extra `modin = ["modin>=0.37.1", "distributed"]` con comentario: modin<=0.37.1 exige pandas<2.4, no coinstala con pandas 3 hasta que modin publique soporte.
- `uv lock` / `uv sync`. Para cada paquete (sobre todo de `analytics`: pandas-bokeh, lux, dtale, sweetviz, pandas-eda, pygwalker, autoviz) que bloquee pandas 3: subir versión si existe una compatible; si no, moverlo a un extra nuevo `analytics-legacy` (decisión del usuario: aislar en extra).
- Verifica `python -c "import pandas, querysource; print(pandas.__version__)"` → 3.x.

**NOT in scope**: tocar código Python; bump de versión del paquete (lo hace `/release`, target 5.2.0).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `pyproject.toml` | MODIFY | pandas>=3.0,<4 en base; quitar modin; extra [modin]; aislar analytics incompatibles |
| `uv.lock` | MODIFY | re-lock |
| `sdd/state/FEAT-161/baseline.txt` | CREATE | resultado de la suite con pandas 2.2 ANTES del cambio |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
(no aplica — sólo manifiestos)

### Existing Signatures to Use
- `pyproject.toml:110` → `    "modin==0.32.0",`
- `pyproject.toml:123` → `analytics = [`
- `pyproject.toml:125` y `:127` (analytics) → `"numpy>=2.0.0,<2.4"` (mantener salvo que el lock lo impida)

### Does NOT Exist
- ~~`pandas` declarado en pyproject~~ — hoy sólo transitivo (asyncdb `pandas>=2.2.3`, geopandas, …).
- ~~modin compatible con pandas 3~~ — 0.37.1 exige `pandas<2.4`.
- ~~extra `[modin]`~~ — lo creas tú.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "pyproject.toml",
      "action": "MODIFY"
    },
    {
      "path": "uv.lock",
      "action": "MODIFY"
    },
    {
      "path": "sdd/state/FEAT-161/baseline.txt",
      "action": "CREATE"
    }
  ],
  "contract_symbols": []
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Captura baseline con pandas 2.2 — *why*: después del cambio ya no se puede reconstruir.
2. Edita pyproject — *why*: declarar el piso explícito impide que un resolver baje pandas.
3. `uv lock` iterativo; aísla lo que no resuelva — *why*: decisión del usuario (aislar, no eliminar).
4. `uv sync --all-extras` excepto `modin` y el extra aislado; confirma import.

### `pyproject.toml` (MODIFY)
```toml
# occurrences: 1 (verified: grep -c '"modin==0.32.0",' pyproject.toml)
# REPLACE `    "modin==0.32.0",` (verified: pyproject.toml:110) WITH:
    "pandas>=3.0,<4",
```
```toml
# occurrences: 1 (verified: grep -c 'analytics = \[' pyproject.toml)
# BEFORE `analytics = [` (verified: pyproject.toml:123) INSERT:
# modin is optional (FEAT-161): modin<=0.37.1 pins pandas<2.4, so it cannot be
# co-installed with pandas>=3 until upstream ships pandas 3 support.
modin = ["modin>=0.37.1", "distributed"]
# FILL IN: analytics-legacy = [...] — only packages proven by `uv lock` to block pandas 3
```
**Why**: el piso explícito es el objetivo; el extra mantiene la puerta abierta a modin.

### FILL IN checklist
- [ ] Lista exacta de paquetes movidos a `analytics-legacy` (con la versión/conflicto observado) — bounded by `uv lock` output
- [ ] Rango de numpy — bounded by lock (pandas 3 requiere numpy>=1.26)

---

## Acceptance Criteria

- [ ] `sdd/state/FEAT-161/baseline.txt` existe y fue generado con pandas 2.2
- [ ] `uv lock` resuelve; `python -c "import pandas; assert int(pandas.__version__.split('.')[0]) >= 3"`
- [ ] modin ausente de dependencias base; extra `[modin]` presente
- [ ] Paquetes aislados listados en la Completion Note

---

## Validation Commands

- `pytest tests/unit/test_multiqs_output_raise.py -q`

---

## Test Specification

(sin tests nuevos — la verificación es el import y el lock)

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
