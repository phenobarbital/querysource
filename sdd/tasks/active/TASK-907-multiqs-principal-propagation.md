# TASK-907: Propagate the MultiQS principal to child queries

**Feature**: FEAT-175 — Querysource Semantic Layer (Ossie models compiled by Sidemantic)
**Spec**: `sdd/specs/semantic-layer.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §8 Q13 (escalated from §9 S2) was answered **"yes, propagate principal to children"**. Today
`MultiQS` holds `self._principal` (multi/__init__.py:244-255) but builds `ThreadQuery` →
`LocalExecutor.execute` → `QueryObject(...)` without it, and `QueryObject.build_provider()` does not
pass a principal to providers. Secured semantic children are therefore denied fail-closed. This
task threads the principal down so library callers' identity reaches child providers (the
semantic provider reads `kwargs['principal']`, TASK-904).

---

## Scope

- `MultiQS`: pass `principal=self._principal` to `ThreadQuery(...)` (multi/__init__.py:734).
- `ThreadQuery.__init__`: keyword `principal=None`, stored; forwarded in `fetch()` to `self._executor.execute(..., principal=...)`.
- `QueryExecutor.execute` / `LocalExecutor.execute` / `RemoteExecutor.execute`: keyword `principal=None`; Local passes it to `QueryObject(..., principal=principal)`; Remote ignores it (FILL IN: log debug that remote children run without principal).
- `QueryObject.build_provider()`: add `"principal": self._principal` to the slug-branch provider args (obj.py:145-154).
- Tests `tests/test_multiqs_principal_propagation.py`.

**NOT in scope**: remote (qworker) principal serialization; any semantic code.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/__init__.py` | MODIFY | pass principal to ThreadQuery |
| `querysource/queries/multi/sources/query.py` | MODIFY | ThreadQuery keeps/forwards principal |
| `querysource/queries/multi/sources/executors.py` | MODIFY | executors accept/forward principal |
| `querysource/queries/obj.py` | MODIFY | provider args gain principal |
| `tests/test_multiqs_principal_propagation.py` | CREATE | tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.queries.obj import QueryObject                       # verified: obj.py:16
from querysource.queries.multi.sources.executors import LocalExecutor, QueryExecutor  # verified: executors.py:49,71
from querysource.queries.multi.sources import ThreadQuery             # verified: multi/__init__.py:33
from querysource.auth.principal import QSPrincipal                    # verified: principal.py:21
```

### Existing Signatures to Use
```python
# querysource/queries/base.py:26-48 — BaseQuery.__init__(slug=None, conditions=None, request=None, loop=None, *,
#   tenant=None, definition=None, principal=None, **kwargs) → stored as self._principal (interfaces/queries.py:129)
# querysource/queries/obj.py:22-41 — QueryObject.__init__(name, query, conditions=None, request=None, queue=None,
#   loop=None, *, tenant=None, **kwargs) → super().__init__(..., tenant=tenant, **kwargs)  (principal flows via **kwargs)
# querysource/queries/obj.py:145-154 — args = {"slug", "query", "qstype", "connection", "definition", "conditions",
#   "request", "loop": self._loop}   (no trailing comma) ; self._qs = provider(**args)
# querysource/queries/multi/sources/executors.py:58-67 — abstract execute(self, name, query, queue, request, *,
#   store=None, definition=None) -> None ; LocalExecutor.execute :80 builds QueryObject(name, query, queue=, request=,
#   loop=, tenant=, definition=) at :107-115 ; RemoteExecutor.execute :159
# querysource/queries/multi/sources/query.py:31-42 — ThreadQuery.__init__(name, query, request, queue,
#   remote_config=None, *, store=None, definition=None, hooks=None); fetch() calls
#   self._executor.execute(self._name, self._query, self._queue, self._request, store=..., definition=...)  (:92-99)
# querysource/queries/multi/__init__.py:734-745 — ThreadQuery(name, query, self._request, self._queue,
#   remote_config=..., store=..., definition=..., hooks=hooks)
```

### Does NOT Exist
- ~~`QueryObject(principal=...)` named parameter~~ — not explicit; it flows through `**kwargs` to `BaseQuery` (verify by test).
- ~~Principal serialization for qworker~~ — out of scope.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/__init__.py", "action": "MODIFY"},
    {"path": "querysource/queries/multi/sources/query.py", "action": "MODIFY"},
    {"path": "querysource/queries/multi/sources/executors.py", "action": "MODIFY"},
    {"path": "querysource/queries/obj.py", "action": "MODIFY"},
    {"path": "tests/test_multiqs_principal_propagation.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/obj.py#QueryObject",
    "sym:querysource/queries/multi/sources/executors.py#LocalExecutor.execute",
    "sym:querysource/queries/multi/sources/executors.py#QueryExecutor.execute",
    "sym:querysource/queries/multi/sources/query.py#ThreadQuery"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- All new parameters keyword-only with default `None` — every existing caller/test keeps working.
- Other executors/subclasses overriding `execute` (search: `grep -rn "def execute" querysource/queries/multi/sources/`) must accept `principal` too.
- Every provider accepts `**kwargs` (abstract.py:55-64), so adding `principal` to provider args is safe.

---

## Implementation Blueprint

### Steps (in order)
1. obj.py args; 2. executors signatures + Local forward; 3. ThreadQuery; 4. MultiQS call site; 5. tests.

### `querysource/queries/obj.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '"loop": self._loop' querysource/queries/obj.py)
# REPLACE `                    "loop": self._loop` (verified: obj.py:153) with:
                    "loop": self._loop,
                    "principal": self._principal,
```

### `querysource/queries/multi/sources/executors.py` (MODIFY)
```python
# occurrences: 3 (verified: grep -c 'definition: LoadedDefinition | None = None,' executors.py) — disambiguate:
# FILL IN: in EACH execute signature (abstract :58, Local :80, Remote :159) add after the `definition` kwarg:
        principal: "QSPrincipal | None" = None,
# and in LocalExecutor's QueryObject(...) call (:107-115) add `principal=principal,` after `definition=definition,`
# (import QSPrincipal under TYPE_CHECKING only)
```

### `querysource/queries/multi/sources/query.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '        hooks: Optional\[SourceHooks\] = None,' query.py)
# AFTER — insert below `        hooks: Optional[SourceHooks] = None,` (query.py:41)
        principal: "QSPrincipal | None" = None,
# AFTER `self._definition = definition` (query.py:52):
        self._principal = principal
# in fetch(): AFTER `            definition=self._definition,` (query.py:98) add:
            principal=self._principal,
```

### `querysource/queries/multi/__init__.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '                        hooks=hooks,' querysource/queries/multi/__init__.py)
# AFTER — insert below `                        hooks=hooks,` (multi/__init__.py:745)
                        principal=self._principal,
```

### FILL IN checklist
- [ ] executors signatures (3) + Local forwarding; Remote debug log
- [ ] verify occurrence counts above before editing
- [ ] Test bodies

---

## Acceptance Criteria

- [ ] `MultiQS(..., principal=p)` → each local child `QueryObject._principal is p` and child provider receives `principal=p`.
- [ ] Callers without principal unchanged; existing MultiQS tests pass.

---

## Validation Commands
- `pytest tests/test_multiqs_principal_propagation.py -q`

---

## Test Specification

```python
# tests/test_multiqs_principal_propagation.py
import pytest
from querysource.auth.principal import QSPrincipal
from querysource.queries.multi.sources.executors import LocalExecutor


async def test_local_executor_forwards_principal(monkeypatch):
    # FILL IN: monkeypatch executors.QueryObject with a recorder; await LocalExecutor().execute(..., principal=p);
    # assert recorded kwargs['principal'] is p


async def test_query_object_passes_principal_to_provider(definitions):
    # FILL IN: fake provider recording kwargs; QueryObject(..., principal=p).build_provider()


async def test_thread_query_forwards_principal(): ...  # FILL IN
```

---

## Agent Instructions

1. Work in the feature worktree.
2. Verify all occurrence counts; implement; validate; commit only listed files.
3. Close with `scripts/sdd/close_task.sh TASK-907 semantic-layer verified`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: implements §8 Q13 answer (spec v1 text said fail-closed follow-up).
