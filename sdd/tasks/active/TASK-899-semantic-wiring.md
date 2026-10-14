# TASK-899: Wiring — get_provider branch, QS principal/SemanticError, ResourceType.SEMANTIC, settings

**Feature**: FEAT-175 — Querysource Semantic Layer (Ossie models compiled by Sidemantic)
**Spec**: `sdd/specs/semantic-layer.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-897
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 7 (code half) and §2 "four contracts" S2/S8. Makes the semantic provider reachable
(`Connection.get_provider` branch), passes `self._principal` to slug-branch providers (S2), lets
`SemanticError` and `QueryAccessDenied` surface with their own codes instead of a wrapped 500 (S8,
AC-22), adds `ResourceType.SEMANTIC` to the shim (AC-18) and the `QS_SEMANTIC_*` settings.

---

## Scope

- `connections.py`: add `elif provider == 'semantic':` before `elif provider in EXTERNAL_PROVIDERS:`.
- `qs.py`: import `SemanticError`; add `"principal": self._principal` to the **slug-branch** args dict; change the slug-branch `except RawQueryPlaceholderError:` to `except (RawQueryPlaceholderError, SemanticError, QueryAccessDenied):`.
- `_resource_types.py`: `SEMANTIC = _StringResourceType("semantic")` in the fallback class, and make `ResourceType.SEMANTIC` available even when the upstream enum is imported (see blueprint).
- `conf.py`: six `QS_SEMANTIC_*` settings after the PBAC block.
- Tests: `tests/auth/test_resource_types_semantic.py`, `tests/semantic/test_wiring.py`.

**NOT in scope**: the provider itself (TASK-904; until then `load_provider('semantic')` raises at import — tests patch it), `pyproject.toml` (TASK-898), MultiQS (TASK-907).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/interfaces/connections.py` | MODIFY | `'semantic'` branch in `get_provider` |
| `querysource/queries/qs.py` | MODIFY | principal pass-through + error re-raise |
| `querysource/auth/_resource_types.py` | MODIFY | `SEMANTIC` |
| `querysource/conf.py` | MODIFY | `QS_SEMANTIC_*` settings |
| `tests/auth/test_resource_types_semantic.py` | CREATE | shim test |
| `tests/semantic/test_wiring.py` | CREATE | get_provider / QS / conf tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.semantic.exceptions import SemanticError, SemanticRequestError  # created by TASK-897
from querysource.exceptions import QueryAccessDenied, RawQueryPlaceholderError  # verified: querysource/exceptions.py:63; qs.py:24-32 import block
from querysource.interfaces.connections import EXTERNAL_PROVIDERS  # verified: connections.py:44
from querysource.auth._resource_types import ResourceType  # verified: _resource_types.py:17-47
from querysource.queries.qs import QS                     # verified: qs.py:50
```

### Existing Signatures to Use
```python
# querysource/interfaces/connections.py:264
async def get_provider(self, entry: dict, session=None, app=None):
    # provider = entry.provider ; 'db' branch (:278) ; `elif provider in EXTERNAL_PROVIDERS:` (:285)
def load_provider(self, provider: str) -> BaseProvider: ...   # :376 — imports querysource.providers.<p>.<p>Provider

# querysource/queries/qs.py:56
def __init__(self, slug='', conditions=None, request=None, loop=None, *, tenant=None,
             definition=None, principal=None, ...)            # stored as self._principal (interfaces/queries.py:129)
# qs.py:303-325 slug branch: args = {..., "loop": self._loop}  (no trailing comma, :310)
#   self._qs = self._provider(**args); await self._qs.prepare_connection(); return self
#   except RawQueryPlaceholderError: raise  (:315)
#   except Exception as err: raise QueryError(..., code=400 if ParserError else 500)

# querysource/auth/_resource_types.py:19-47 — try: upstream ResourceType + _check tuple; except: _StringResourceType shim
# querysource/conf.py:475-477 — QS_PBAC_ALLOW_SESSIONLESS_AUTHZ = config.getboolean('...', fallback=False)
```

### Does NOT Exist
- ~~`ResourceType.SEMANTIC`~~ — neither shim nor upstream; added here.
- ~~`QS_SEMANTIC_*`~~ — added here.
- ~~`Connection.get_provider` special-casing `'multi'`~~.
- ~~`BASE_DIR` assumed~~ — FILL IN: verify `BASE_DIR` is defined in `conf.py` before using it (`grep -n '^BASE_DIR' querysource/conf.py`).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/interfaces/connections.py", "action": "MODIFY"},
    {"path": "querysource/queries/qs.py", "action": "MODIFY"},
    {"path": "querysource/auth/_resource_types.py", "action": "MODIFY"},
    {"path": "querysource/conf.py", "action": "MODIFY"},
    {"path": "tests/auth/test_resource_types_semantic.py", "action": "CREATE"},
    {"path": "tests/semantic/test_wiring.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/interfaces/connections.py#Connection.get_provider",
    "sym:querysource/queries/qs.py#QS.build_provider",
    "sym:querysource/auth/_resource_types.py#ResourceType"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **Deviation flagged (spec gap):** spec M7 re-raises only `SemanticError`, but M5 raises `QueryAccessDenied` (404) on slug/datasource deny from inside `prepare_connection()`, which the generic `except Exception` would wrap into a 500, contradicting AC-10/AC-22. Add `QueryAccessDenied` to the re-raise tuple (it is already imported in `qs.py:28`). Record this in the Completion Note.
- Only the **slug** branch changes; the `query`/`raw` branch (qs.py:358, :364) stays untouched.
- Upstream `ResourceType` is an enum that may lack `SEMANTIC`: the semantic code must not fail with `AttributeError` when navigator-auth is installed. Export a module-level fallback (see blueprint).

---

## Implementation Blueprint

### Steps (in order)
1. Edit `connections.py` — *why*: without the branch, `'semantic'` falls through to `default_driver('semantic')` and logs an exception (§7 gotcha).
2. Edit `qs.py` (import, args, except) — *why*: S2 + S8.
3. Edit `_resource_types.py` — *why*: AC-18.
4. Edit `conf.py` — *why*: TASK-901/904 read these settings.
5. Write tests, run validation.

### `querysource/interfaces/connections.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '        elif provider in EXTERNAL_PROVIDERS:' querysource/interfaces/connections.py)
# BEFORE — insert above `        elif provider in EXTERNAL_PROVIDERS:` (verified: connections.py:285)
        elif provider == 'semantic':
            # FEAT-175: the semantic provider resolves its own datasource.
            _provider = self.load_provider('semantic')
            return [None, _provider]
```

### `querysource/queries/qs.py` (MODIFY) — import
```python
# occurrences: 1 (verified: grep -c '    RawQueryPlaceholderError,' querysource/queries/qs.py)
# AFTER — insert below the `)` that closes `from ..exceptions import (` (`    RawQueryPlaceholderError,` is at qs.py:31, `)` at :32)
from ..semantic.exceptions import SemanticError
```

### `querysource/queries/qs.py` (MODIFY) — principal in slug args
```python
# occurrences: 2 (verified: grep -c '"loop": self._loop' querysource/queries/qs.py) — disambiguated:
# the slug branch is the one WITHOUT trailing comma, followed by `}` and `self._qs = self._provider(**args)` (qs.py:310-312)
                    "request": self._request,
                    "loop": self._loop,
                    "principal": self._principal,
                }
                self._qs = self._provider(**args)
```

### `querysource/queries/qs.py` (MODIFY) — re-raise
```python
# occurrences: 2 (verified: grep -c 'except RawQueryPlaceholderError:' querysource/queries/qs.py) — disambiguated:
# the FIRST one, preceded by `await self._qs.prepare_connection()` / `return self` in the slug branch (qs.py:313-315)
                await self._qs.prepare_connection()
                return self
            except (RawQueryPlaceholderError, SemanticError, QueryAccessDenied):
                # Operational / semantic / access errors surface with their own code (FEAT-175 S8).
                raise
```
**Why**: every BaseProvider subclass accepts `**kwargs` (abstract.py:55-64) so the extra `principal` key is harmless (AC-25).

### `querysource/auth/_resource_types.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'RAW_QUERY = _StringResourceType("raw_query")' querysource/auth/_resource_types.py)
# AFTER — insert below `        RAW_QUERY = _StringResourceType("raw_query")` (verified: _resource_types.py:45)
        SEMANTIC = _StringResourceType("semantic")
```
```python
# APPEND at end of module (after the try/except):
if not hasattr(ResourceType, "SEMANTIC"):
    # FILL IN: upstream enum lacks SEMANTIC (FEAT-175 shim). Make ResourceType.SEMANTIC resolve to a
    # str "semantic" usable by navigator-auth check_access — e.g. setattr on a non-enum or a
    # module-level SEMANTIC_RESOURCE constant consumed by governance.py — bounded by AC-18 (do NOT
    # extend the `_check` tuple)
```

### `querysource/conf.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'QS_PBAC_ALLOW_SESSIONLESS_AUTHZ = config.getboolean(' querysource/conf.py)
# AFTER — insert below the closing `)` of that call (conf.py:475-477)

# ── Semantic layer (FEAT-175) ─────────────────────────────────────────────────
QS_SEMANTIC_MODELS_DIR = config.get('QS_SEMANTIC_MODELS_DIR', fallback=str(BASE_DIR / 'semantic'))
QS_SEMANTIC_DEFAULT_LIMIT = config.getint('QS_SEMANTIC_DEFAULT_LIMIT', fallback=500)
QS_SEMANTIC_MAX_LIMIT = config.getint('QS_SEMANTIC_MAX_LIMIT', fallback=5000)
QS_SEMANTIC_MAX_FILTERS = config.getint('QS_SEMANTIC_MAX_FILTERS', fallback=32)
QS_SEMANTIC_CACHE_SIZE = config.getint('QS_SEMANTIC_CACHE_SIZE', fallback=64)
QS_SEMANTIC_MAX_MODEL_BYTES = config.getint('QS_SEMANTIC_MAX_MODEL_BYTES', fallback=2 * 1024 * 1024)
```

### FILL IN checklist
- [ ] `BASE_DIR` exists in `conf.py` (else use the module's project-root variable)
- [ ] `_resource_types.py` upstream-enum fallback for `SEMANTIC`
- [ ] Test bodies below

---

## Acceptance Criteria

- [ ] `get_provider(AttrDict(provider='semantic'))` → `[None, <semantic provider>]` without calling `default_driver` (AC-2).
- [ ] `QS(slug, principal=p)` passes `principal=p` to the provider (AC-25).
- [ ] A `SemanticRequestError` raised in `prepare_connection()` reaches the caller with `code == 400` (AC-22).
- [ ] `ResourceType.SEMANTIC == "semantic"` (AC-18); settings default as specified.
- [ ] `ruff check` clean on touched files.

---

## Validation Commands
- `pytest tests/auth/test_resource_types_semantic.py -q`
- `pytest tests/semantic/test_wiring.py -q`
- `pytest tests/test_route_registration.py -q`

---

## Test Specification

```python
# tests/semantic/test_wiring.py
import sys, types
import pytest
from datamodel.typedefs import AttrDict
from querysource.semantic.exceptions import SemanticRequestError


async def test_get_provider_semantic_branch(monkeypatch):
    # FILL IN: build a Connection (querysource.interfaces.connections.Connection), monkeypatch
    # load_provider to return a sentinel class and default_driver to raise; assert result == [None, sentinel]


async def test_provider_receives_principal(definitions, monkeypatch):
    # FILL IN: definitions.add(query_slug="s", provider="semantic", query_raw="x"); inject a fake module
    # sys.modules['querysource.providers.semantic'] with a provider class recording kwargs;
    # QS(slug="s", principal=p).build_provider(); assert recorded['principal'] is p


async def test_provider_semantic_error_surfaces_with_code(definitions, monkeypatch):
    # FILL IN: fake provider whose prepare_connection raises SemanticRequestError("bad");
    # with pytest.raises(SemanticRequestError) as e: await QS(slug=...).build_provider(); assert e.value.code == 400


def test_conf_semantic_defaults():
    from querysource import conf
    assert conf.QS_SEMANTIC_DEFAULT_LIMIT == 500 and conf.QS_SEMANTIC_MAX_LIMIT == 5000
```
```python
# tests/auth/test_resource_types_semantic.py
from querysource.auth._resource_types import ResourceType


def test_resource_type_semantic_shim():
    assert ResourceType.SEMANTIC == "semantic"
```

---

## Agent Instructions

1. Work in the feature worktree; confirm TASK-897 is done.
2. Verify anchors, implement, complete FILL INs, run validation, commit only listed files.
3. Close with `scripts/sdd/close_task.sh TASK-899 semantic-layer verified`; record the `QueryAccessDenied` deviation.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: `QueryAccessDenied` added to the slug-branch re-raise tuple (see Key Constraints).
