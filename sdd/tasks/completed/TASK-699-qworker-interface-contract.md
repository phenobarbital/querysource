# TASK-699: QWorker Interface Contract Documentation

**Feature**: FEAT-101 — MultiQuery Remote Execution
**Spec**: `sdd/specs/multiquery-remote-execution.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

> Documents the required qworker-side QueryTask handler as a formal interface contract.
> This document will be used by the qworker team/repo to implement the handler.
> Implements Spec §3 (Module 7) and the Qworker Interface Contract section.

---

## Scope

- Create `sdd/contracts/qworker-query-handler.md` documenting:
  - Handler signature: `async def query_handler(slug, conditions, **options) -> pd.DataFrame`
  - Input format: `{slug: str, conditions: dict, options: dict}`
  - Execution flow: QueryObject instantiation → build_provider → query → return DataFrame
  - Error contract: which exceptions propagate, which are wrapped
  - v2 streaming extension: Redis stream chunked-row protocol (future, documented for design continuity)
  - Prerequisites: qworker must have querysource installed with configured credentials
- This is documentation only — no implementation code

**NOT in scope**: Actual qworker implementation, RemoteExecutor code (TASK-694),
querysource code changes

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `sdd/contracts/qworker-query-handler.md` | CREATE | Interface contract document |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# The contract document references these qworker-side signatures:
# qw/server.py — connection_handler() routes tasks to executors
# qw/executor/__init__.py — TaskExecutor.run() executes callables
# qw/wrappers/func.py — FuncWrapper wraps functions for transport

# The handler on the qworker side will use:
from querysource.queries.obj import QueryObject  # querysource/queries/obj.py:20
```

### Existing Signatures to Use
```python
# querysource/queries/obj.py:20 — what the handler will instantiate:
class QueryObject(BaseQuery):
    def __init__(self, name, query, conditions=None, request=None,
                 queue=None, loop=None):                                   # line 26
    async def build_provider(self):                                        # line 65
    async def query(self):                                                 # line 183

# qw/client.py:326 — how the client side calls:
async def run(self, fn: Any, *args, use_wrapper: bool = False, **kwargs):
    # Serializes fn via cloudpickle, sends to worker, waits for result
```

### Does NOT Exist
- ~~`qw.handlers.query`~~ — no query handler module exists in qworker; the contract describes what needs to be created
- ~~`qw.tasks.query_handler`~~ — does not exist
- ~~`QueryTask`~~ — does not exist as a class; the contract defines the interface

---

## Implementation Notes

### Key Constraints
- The document must be clear enough for a developer unfamiliar with querysource
  to implement the handler on the qworker side
- Include example usage showing how QClient.run() calls the handler
- Document the v2 streaming extension even though it's future work — this ensures
  the handler design accommodates it

### References in Codebase
- `sdd/specs/multiquery-remote-execution.spec.md` — Qworker Interface Contract section
- `qw/client.py` — QClient that calls the handler
- `qw/server.py` — QWorker server that routes to handlers
- `querysource/queries/obj.py` — QueryObject that the handler wraps

---

## Acceptance Criteria

- [ ] `sdd/contracts/qworker-query-handler.md` created with complete interface contract
- [ ] Handler signature documented
- [ ] Input/output format documented
- [ ] Error contract documented (which exceptions propagate)
- [ ] v2 streaming extension documented
- [ ] Prerequisites (querysource install, credentials) documented
- [ ] Example usage showing QClient → handler → result flow

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-remote-execution.spec.md`, especially the
   "Qworker Interface Contract" section
2. **Check dependencies** — this task has none
3. **Write the contract document** following the spec's contract section as the source of truth
4. **Update status** in `sdd/tasks/index/multiquery-remote-execution.json` → `"in-progress"`
5. **Move this file** to `sdd/tasks/completed/TASK-699-qworker-interface-contract.md` when done
6. **Update index** → `"done"`
7. **Fill in the Completion Note** below

---

## Completion Note

**Completed by**: claude-sonnet-4-6
**Date**: 2026-05-26
**Notes**: Created sdd/contracts/qworker-query-handler.md with full handler contract: signature, execution flow (QueryObject instantiation → build_provider → query), input/output format, error contract, raw query support (per open question §8 resolution), example usage, and v2 streaming extension documentation. No code changes.

**Deviations from spec**: Added raw query support documentation (per open question in spec §8 resolved by the author: "both, queries + slugs").

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
