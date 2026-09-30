# TASK-817: PBAC gate for write-capable Output destinations

**Feature**: FEAT-155 — MultiQuery TableDelete Destination
**Spec**: `sdd/specs/multi-tabledelete.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 3. Destinations only receive `data` and config kwargs, with no
principal or session, so the permission check for a destination that removes rows
must happen before any child runs, inside `MultiQS._preflight_principal`. When
`self._options["Output"]` contains a step listed in `WRITE_DESTINATIONS`, the
caller must hold `datasource:use` on `pg_admin`. That is the existing FEAT-091
admin grant (`querysource/datasources/drivers/pg_admin.py:1-10`).

**Two entry points, both gated (added after task review).** HTTP requests do NOT reach
`_preflight_principal`: `QueryHandler` builds `MultiQS(...)` without `principal=`
(`querysource/handlers/multi.py:493`), so `self._principal` is None and the MultiQS-level gate
returns early. HTTP calls are authorised by `QueryHandler._preflight_multiquery`
(`handlers/multi.py:29`, Guardian-based). The gate is therefore added in **both** places:
- `MultiQS._preflight_principal`: principal-based callers (scheduler, internal).
- `QueryHandler._preflight_multiquery`: HTTP. A new keyword-only `write_access: bool = False`
  parameter; when true, `await self._enforce_pbac(request, resource_type=ResourceType.DATASOURCE,
  resource_name="pg_admin", action="datasource:use")` inside the existing fail-closed `try`.
  The call site (`handlers/multi.py:458`) computes it from the **inline** payload's `Output`.
  Stored multi slugs (`slug=...`) are not inspected at HTTP level: their `Output` comes from
  the stored definition, authored under the definition-save permissions. The request cannot
  inject `Output` into a stored slug, because request data only becomes conditions.

The gate only compares step *names* (strings). It does not import or reference any
destination class, so it runs concurrently with the destination task. FEAT-156
(`multi-executesql`) later adds `"ExecuteSQL"` to the same frozenset.

---

## Scope

- Add the module-level `WRITE_DESTINATIONS: frozenset[str] = frozenset({"TableDelete"})` and
  the module-level helper `_output_step_names(output: object) -> set[str]` to
  `querysource/queries/multi/__init__.py`.
- In `_preflight_principal`, insert the gate right before `if has_raw_child:`. It makes one
  `enforce_principal(self._principal, ResourceType.DATASOURCE, "pg_admin", "datasource:use", tenant=…, logger=…)` call.
- Add `tests/test_multiqs_write_gate.py` with `test_preflight_gate_enforced` and `test_preflight_gate_skipped`.
- In `querysource/handlers/multi.py`:
  - add keyword-only `write_access: bool = False` to `_preflight_multiquery` and the
    `datasource:use` on `pg_admin` check after the raw-query check;
  - at the call site pass
    `write_access=bool(not slug and isinstance(options, dict) and _output_step_names(options.get("Output")) & WRITE_DESTINATIONS)`.
- Add `tests/test_multiquery_write_gate_http.py`: handler-level enforced / skipped / PBAC-disabled no-op tests.

**NOT in scope**: gating the existing `Table` destination (spec §8, resolved: no); a new
`datasource:write` action; any change to the Output dispatch loop.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/__init__.py` | MODIFY | `WRITE_DESTINATIONS`, `_output_step_names`, gate in `_preflight_principal` |
| `tests/test_multiqs_write_gate.py` | CREATE | gate enforced / skipped tests |
| `querysource/handlers/multi.py` | MODIFY | `write_access` param + `pg_admin` check in `_preflight_multiquery`; compute it at the call site |
| `tests/test_multiquery_write_gate_http.py` | CREATE | HTTP-level gate tests |

---

## Codebase Contract (Anti-Hallucination)

Re-verified against HEAD `f8a32ae`.

### Verified Imports
```python
# Local imports already present inside _preflight_principal — reuse them, add no new import:
from ...auth._resource_types import ResourceType          # verified: querysource/queries/multi/__init__.py:248
from ...auth.enforcement import enforce_principal         # verified: querysource/queries/multi/__init__.py:249

# Tests:
import querysource.auth.enforcement as enforcement        # verified: tests/multi/test_multiqs_principal.py:6 (monkeypatch target)
from querysource.auth.principal import QSPrincipal        # verified: tests/multi/test_multiqs_principal.py:7
from querysource.auth._resource_types import ResourceType # verified: querysource/auth/_resource_types.py:43 (DATASOURCE)
from querysource.exceptions import QueryAccessDenied      # verified: querysource/exceptions.py:63
from querysource.queries.multi import MultiQS             # verified: tests/multi/test_multiqs_principal.py:9
```

### Existing Signatures to Use
```python
# querysource/queries/multi/__init__.py
_INFRA_ERROR_TYPES = frozenset({...})                     # lines 47-49 (module-level frozenset precedent)
def classify_output_error(exc: BaseException) -> str | None:  # line 52
def get_operator_module(clsname: str):                    # line 69
class MultiQS(BaseQuery):                                 # line 101
    def __init__(self, slug=None, queries=None, files=None, query: dict | None = None, ..., *,
                 tenant=None, definition=None, principal=None, **kwargs)  # line 107; self._options = query or {} (:142) after popping queries/files/sources
    async def _preflight_principal(self) -> None:         # line 240 — early `return` when self._principal is None (:246-247)
        has_raw_child = False                             # line 251
        if has_raw_child:                                 # line 272 — anchor
    async def query(self):                                # line 278
        await self._preflight_principal()                 # line 377 — self._options (incl. "Output") already populated
        _output = self._options.pop('Output', None)       # line 747 — Output shape: list of {step_name: cfg}

# querysource/interfaces/queries.py
self._tenant_selector = tenant                            # line 120
self._principal = principal                               # line 129

# querysource/auth/enforcement.py:134
async def enforce_principal(principal, resource_type, resource_name: str, action: str, *,
                            tenant: str | None = None, logger=None) -> AccessDecision  # deny -> QueryAccessDenied
class AccessDecision                                      # line 22 — AccessDecision(allowed=True, pbac_enabled=True)

# querysource/auth/_resource_types.py
ResourceType.DATASOURCE                                   # line 43
```

```python
# querysource/handlers/multi.py
from ..auth import ResourceType                              # line 9 (already imported)
from ..queries import MultiQS                                # line 20 (add: from ..queries.multi import WRITE_DESTINATIONS, _output_step_names)
async def _preflight_multiquery(self, request, slugs: list, files: list, has_raw_query: bool) -> None:  # line 29
    guardian = request.app.get('security'); if guardian is None: return   # PBAC disabled fast-path (:54-56)
    if has_raw_query: await self._enforce_pbac(request, resource_type=ResourceType.RAW_QUERY, resource_name="raw_query", action="raw_query:execute")  # :90-96
    except web.HTTPNotFound: raise / except Exception → HTTPNotFound (fail-closed)   # :97-103
await self._preflight_multiquery(request, slugs=..., files=..., has_raw_query=_has_raw)  # call site :458
qs = MultiQS(slug=..., queries=..., query=options, ...)      # :493 — no principal= (why the handler gate is needed)
# querysource/handlers/abstract.py:326
async def _enforce_pbac(self, request, resource_type, resource_name: str, action: str) -> None  # raises HTTPNotFound on deny
# tests/handlers/test_multiquery_pbac_smoke.py — pattern for handler preflight tests (_make_handler, :16)
```

### Does NOT Exist
- ~~`WRITE_DESTINATIONS` / `_output_step_names`~~ — created by this task.
- ~~`ResourceType.DESTINATION` / `ResourceType.OUTPUT`~~ — only SLUG, DATASOURCE, DRIVER and RAW_QUERY exist (`_resource_types.py:42-45`).
- ~~A destination-level PBAC hook~~ — destinations get no principal, so the gate lives only in `_preflight_principal`.
- ~~A `datasource:write` action~~ — use `datasource:use` on `pg_admin`.
- ~~`principal=` passed by `QueryHandler` to `MultiQS`~~ — it is not (`handlers/multi.py:493`); do not add it here (out of scope), gate in the handler instead.
- ~~An import of any destination class in this module for the gate~~ — compare step-name strings only.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/__init__.py", "action": "MODIFY"},
    {"path": "tests/test_multiqs_write_gate.py", "action": "CREATE"},
    {"path": "querysource/handlers/multi.py", "action": "MODIFY"},
    {"path": "tests/test_multiquery_write_gate_http.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/__init__.py#MultiQS",
    "sym:querysource/queries/multi/__init__.py#MultiQS._preflight_principal",
    "sym:querysource/auth/enforcement.py#enforce_principal",
    "sym:querysource/auth/_resource_types.py#ResourceType",
    "sym:querysource/handlers/multi.py#QueryHandler._preflight_multiquery",
    "sym:querysource/handlers/abstract.py#AbstractHandler._enforce_pbac"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- With no principal, behaviour is unchanged: the existing early `return` covers it, so do not move the gate above it.
- Existing `Table`-only pipelines get no extra `enforce_principal` call (spec §8).
- The deny must raise before any child is dispatched. `_preflight_principal` is awaited at `:377`, before the definition-repository preflight and dispatch.
- `_output_step_names` must tolerate a malformed `Output` (None, a dict, or non-dict entries) and return an empty set rather than raising, because `Output` validation belongs to the Output loop.

---

## Implementation Blueprint

### Steps (in order)
1. Add `WRITE_DESTINATIONS` and `_output_step_names` above `get_operator_module`. *Why*: they are module-level so FEAT-156 can extend the set in one place, and tests can import them.
2. Insert the gate before `if has_raw_child:` in `_preflight_principal`. *Why*: it reuses the local `ResourceType`/`enforce_principal` imports and runs after the principal-None early return.
3. Create `tests/test_multiqs_write_gate.py`. *Why*: it covers spec §4 M3 (enforced, denied up-front, skipped).
4. Add the handler-level gate in `querysource/handlers/multi.py` and create `tests/test_multiquery_write_gate_http.py`. *Why*: HTTP calls never carry a principal into MultiQS, so the MultiQS gate alone does not protect the API.
5. Run the Validation Commands and `ruff check querysource/queries/multi/__init__.py querysource/handlers/multi.py`.

### `querysource/queries/multi/__init__.py` (MODIFY — module level)
```python
# occurrences: 1 (verified: grep -c '^def get_operator_module(clsname: str):$' querysource/queries/multi/__init__.py)
# BEFORE — insert above `def get_operator_module(clsname: str):` (verified: querysource/queries/multi/__init__.py:69),
# i.e. after classify_output_error; keep two blank lines around it.

# FEAT-155: Output step names that modify data on the full-access DB* connection.
# A MultiQuery using any of them requires ``datasource:use`` on ``pg_admin``
# (FEAT-091) at pre-flight. FEAT-156 adds "ExecuteSQL".
WRITE_DESTINATIONS: frozenset[str] = frozenset({"TableDelete"})


def _output_step_names(output: object) -> set[str]:
    """Return the step names of an ``Output`` list (``[{name: cfg}, …]``); ignore malformed entries.

    Args:
        output: The raw ``Output`` option (normally a list of single-key dicts).

    Returns:
        The set of step names; empty when ``output`` is not a list/tuple.
    """
    if not isinstance(output, (list, tuple)):
        return set()
    return {
        name
        for step in output
        if isinstance(step, dict)
        for name in step
        if isinstance(name, str)
    }
```
**Why**: this matches the spec §3 M3 skeleton. A frozenset follows the `_DATA_ERROR_TYPES` / `_INFRA_ERROR_TYPES` precedent (lines 46-49).

### `querysource/queries/multi/__init__.py` (MODIFY — `_preflight_principal`)
```python
# occurrences: 1 (verified: grep -c '        if has_raw_child:' querysource/queries/multi/__init__.py)
# BEFORE — insert above `        if has_raw_child:` (verified: querysource/queries/multi/__init__.py:272)

        # FEAT-155: a write-capable Output step needs the admin-datasource grant.
        if _output_step_names((self._options or {}).get("Output")) & WRITE_DESTINATIONS:
            await enforce_principal(
                self._principal, ResourceType.DATASOURCE, "pg_admin", "datasource:use",
                tenant=self._tenant_selector, logger=self._logger,
            )

```
**Why**: the gate uses the same call shape as the existing `slug:execute` and `raw_query:execute` checks (lines 255-276). `enforce_principal` raises `QueryAccessDenied` on deny, which aborts `query()` before any child runs. The spec's line numbers drifted (`_preflight_principal` :239 → :240, `if has_raw_child:` :271 → :272), but the anchor is still unique.

### `tests/test_multiqs_write_gate.py` (CREATE)
```python
"""FEAT-155 / TASK-817: PBAC pre-flight gate for write-capable Output destinations."""
from unittest.mock import AsyncMock

import pytest

import querysource.auth.enforcement as enforcement
from querysource.auth._resource_types import ResourceType
from querysource.auth.principal import QSPrincipal
from querysource.exceptions import QueryAccessDenied
from querysource.queries.multi import MultiQS, WRITE_DESTINATIONS, _output_step_names


@pytest.fixture
def principal():
    return QSPrincipal(user_id="35", groups=("sales",))


def _pipeline(output: list) -> dict:
    return {"queries": {"a": {"slug": "report_a"}}, "Output": output}


DELETE_STEP = {"TableDelete": {"schema": "s", "table": "t", "pk": ["id"]}}
TABLE_STEP = {"Table": {"schema": "s", "table": "t", "method": "append"}}


async def test_preflight_gate_enforced(principal, monkeypatch):
    # FILL IN: (1) allow mock = AsyncMock(return_value=enforcement.AccessDecision(allowed=True, pbac_enabled=True)); monkeypatch enforcement.enforce_principal;
    #          await MultiQS(query=_pipeline([DELETE_STEP, TABLE_STEP]), principal=principal)._preflight_principal();
    #          assert one awaited call has args[1:4] == (ResourceType.DATASOURCE, "pg_admin", "datasource:use")
    #          (2) deny: side_effect raises QueryAccessDenied only for ResourceType.DATASOURCE; set mqs.get_definition_repository to a coroutine that raises AssertionError;
    #          `await mqs.query()` raises QueryAccessDenied (no child ran)
    ...


async def test_preflight_gate_skipped(principal, monkeypatch):
    # FILL IN: (1) Table-only Output with principal -> no call has ResourceType.DATASOURCE (only the slug:execute call)
    #          (2) MultiQS(query=_pipeline([DELETE_STEP])) with no principal -> mock not awaited
    #          (3) _output_step_names(None) == set(), _output_step_names([DELETE_STEP, "bad"]) == {"TableDelete"}; "TableDelete" in WRITE_DESTINATIONS
    ...
```
**Why**: this follows `tests/multi/test_multiqs_principal.py`, which monkeypatches `enforcement.enforce_principal`. That works because `_preflight_principal` imports the function at call time. Pass `query=` so `Output` lands in `self._options` (`MultiQS.__init__` pops only `queries`, `files` and `sources`).

### `querysource/handlers/multi.py` (MODIFY — signature, check, call site, import)
```python
# occurrences: 1 (verified: grep -c '^from ..queries import MultiQS$' querysource/handlers/multi.py)
# AFTER — insert below `from ..queries import MultiQS` (verified: handlers/multi.py:20)
from ..queries.multi import WRITE_DESTINATIONS, _output_step_names

# occurrences: 2 (verified: grep -c '        has_raw_query: bool,' querysource/handlers/multi.py) — AMBIGUOUS:
#   the second hit is `_preflight_multiquery_owned` (:109). Disambiguate with the method header:
#       async def _preflight_multiquery(
#           self,
#           request: web.Request,
#           slugs: list,
#           files: list,
#           has_raw_query: bool,
#       ) -> None:                                   (handlers/multi.py:29-34)
# REPLACE only that signature's tail `has_raw_query: bool,\n    ) -> None:` with:
        has_raw_query: bool,
        *,
        write_access: bool = False,
    ) -> None:
# docstring Args: add `write_access: True when the inline Output uses a WRITE_DESTINATIONS step (FEAT-155); triggers datasource:use on pg_admin.`

# occurrences: 1 (verified: grep -c '            if has_raw_query:' querysource/handlers/multi.py)
# AFTER the whole `if has_raw_query:` block (ends at handlers/multi.py:96), still inside the `try`:
            if write_access:
                # FEAT-155: write-capable Output steps run on DB* credentials.
                await self._enforce_pbac(
                    request,
                    resource_type=ResourceType.DATASOURCE,
                    resource_name="pg_admin",
                    action="datasource:use",
                )

# occurrences: 2 (verified: grep -c '            has_raw_query=_has_raw,' querysource/handlers/multi.py) — AMBIGUOUS:
#   the second hit (:481) belongs to `_preflight_multiquery_owned(...)` — do NOT touch it. Target the first call:
#       await self._preflight_multiquery(
#           request,
#           slugs=list((_queries or {}).keys()),
#           files=list((_files or {}).keys()),
#           has_raw_query=_has_raw,              (handlers/multi.py:458-462)
# REPLACE that call's `has_raw_query=_has_raw,` line with:
            has_raw_query=_has_raw,
            write_access=bool(
                not slug and isinstance(options, dict)
                and _output_step_names(options.get("Output")) & WRITE_DESTINATIONS
            ),
```
**Why**: HTTP requests never carry a principal into MultiQS, so without this the gate would not protect the API at all. Placing the check inside the existing `try` keeps the fail-closed behaviour: Guardian errors become 404. `_enforce_pbac` is already a no-op when PBAC is disabled. FILL IN: verify `from ..queries.multi import ...` introduces no import cycle (`handlers/multi.py` already imports `..queries.multi.operators`, so it should not).

### `tests/test_multiquery_write_gate_http.py` (CREATE)
```python
"""FEAT-155 / TASK-817: HTTP-level (handler) write-destination gate."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from querysource.auth import ResourceType
from querysource.handlers.multi import QueryHandler


def _make_handler():
    # FILL IN: mirror tests/handlers/test_multiquery_pbac_smoke.py::_make_handler (:16)
    ...


async def test_write_access_enforces_pg_admin():
    # FILL IN: guardian present, session present; _enforce_pbac mocked (AsyncMock);
    #          await h._preflight_multiquery(request, slugs=[], files=[], has_raw_query=False, write_access=True)
    #          assert _enforce_pbac awaited with resource_type=ResourceType.DATASOURCE, resource_name="pg_admin", action="datasource:use"
    ...


async def test_write_access_denied_raises_404():
    # FILL IN: _enforce_pbac side_effect=web.HTTPNotFound() -> pytest.raises(web.HTTPNotFound)
    ...


async def test_write_access_false_no_extra_check():
    # FILL IN: write_access omitted -> no DATASOURCE call; PBAC disabled (guardian None) -> no call at all
    ...
```
**Why**: this covers the HTTP path the MultiQS-level tests cannot reach. The handler class is `QueryHandler` (`handlers/multi.py:27`), which the smoke test also builds.

### FILL IN checklist
- [ ] `handlers/multi.py`: import has no cycle; call-site expression uses the local names `slug` / `options` in scope at :458.
- [ ] `tests/test_multiquery_write_gate_http.py`: three tests implemented from the smoke-test pattern.
- [ ] `tests/test_multiqs_write_gate.py::test_preflight_gate_enforced`: allowed-call assertion plus deny-before-children assertion (AC: denied up-front).
- [ ] `tests/test_multiqs_write_gate.py::test_preflight_gate_skipped`: Table-only case, no-principal case, helper edge cases.

---

## Acceptance Criteria

- [ ] A MultiQuery with a `TableDelete` output step is denied up-front, before any child query runs, for a principal without `datasource:use` on `pg_admin`.
- [ ] The same holds for **HTTP** requests: an inline pipeline with a `TableDelete` Output step gets 404 from `_preflight_multiquery` when the caller lacks `datasource:use` on `pg_admin`, and nothing executes.
- [ ] A `Table`-only Output adds no `enforce_principal` call, and `principal=None` makes no call at all.
- [ ] Existing PBAC pre-flight tests still pass (`tests/multi/test_multiqs_principal.py`).
- [ ] `ruff check querysource/queries/multi/__init__.py` is clean.

## Validation Commands

- `pytest tests/test_multiqs_write_gate.py -q`
- `pytest tests/multi/test_multiqs_principal.py -q`
- `pytest tests/test_multiqs_destination_dispatch.py -q`
- `pytest tests/test_multiquery_write_gate_http.py -q`
- `pytest tests/handlers/test_multiquery_pbac_smoke.py -q`

---

## Test Specification

See the `tests/test_multiqs_write_gate.py` blueprint block above. It covers
`test_preflight_gate_enforced` and `test_preflight_gate_skipped` from spec §4 (M3).
`asyncio_mode = auto` (`pytest.ini`), so the tests need no `@pytest.mark.asyncio`.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree**, never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug multi-tabledelete --feature-id FEAT-155`).
2. **Read the spec** at the path listed above for full context.
3. **Check dependencies**: none for this task (`sdd/tasks/index/multi-tabledelete.json`).
4. **Verify the Codebase Contract**: re-run both `grep -c` anchors. If a count is not 1, re-locate the anchor before editing.
5. **Update status** in `sdd/tasks/index/multi-tabledelete.json` to `"in-progress"` (set `started_at`) and commit only that index file.
6. **Implement** from the blueprint and complete every `# FILL IN:`. Never change a signature or path the blueprint fixes.
7. **Verify** by running the Validation Commands.
8. **Commit the code**, staging only the files this task lists (never `git add .` / `-A`).
9. **Close the task** with `scripts/sdd/close_task.sh TASK-817 multi-tabledelete verified`.
10. **Fill in the Completion Note** below, then commit the staged SDD state.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
