# TASK-933: Durable pause on the slug definition (repository)

**Feature**: FEAT-181 — Scheduler Execution History
**Spec**: `sdd/specs/scheduler-execution-history.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §2 "Durable pause" and §3 Module 4. Pause state is configuration, not logs. It
lives on the slug's existing row as `attributes.scheduler.paused` (flowtask
`troc.jobs.enabled` precedent), with no new tables. `DefinitionRepository.patch`
replaces whole columns today, so a slug edit could silently resume a paused job; the
pause keys must survive it.

---

## Scope

- Add `DefinitionRepository.schedule_state(store, slug) -> (present, paused)`, one
  `SELECT` that replaces `exists()` on the scheduler fire path.
- Add `DefinitionRepository.set_schedule_paused(store, slug, *, paused, user, reason)`:
  an atomic `jsonb_set` of `attributes.scheduler.paused`, `paused_by`, `paused_at`
  and `pause_reason`. It returns False when the slug is missing.
- In `patch`, when `data` contains `attributes` whose `scheduler` lacks `paused*` keys
  but the stored row has them, carry the stored keys forward.
- Tests.

**NOT in scope**: scheduler/handler wiring (TASK-937/939).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/repositories/definitions.py` | MODIFY | `schedule_state`, `set_schedule_paused`, `patch` preservation |
| `tests/scheduler/test_durable_pause_repository.py` | CREATE | Repository tests with a fake connection |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.repositories import DefinitionRepository     # verified: querysource/scheduler/scheduler.py:40
from querysource.tenants import QueryStore                     # scheduler.py:49-55
```

### Existing Signatures to Use
```python
# querysource/repositories/definitions.py
class DefinitionRepository:
    def __init__(..., connection_factory: Callable[[], Awaitable[AbstractAsyncContextManager[pg]]])  # line 116; self.connection_factory line 120
    def _qualified_table(self, store: QueryStore) -> str                 # line 124
    async def _fetch_row(self, store, slug) -> Mapping | None            # line 181
    async def exists(self, store: QueryStore, slug: str) -> bool         # line 188: SELECT 1 ... ; conn.fetch_one(sql, slug)
    def _translate_write_error(self, err, store) -> None                 # line 194
    async def patch(self, identity, data, *, run_as_actor=None, request_info=None) -> Mapping[str, Any]   # line 518
        # builds `SET <col> = $n` per key (line ~561-567); attributes is replaced wholesale
# attributes column is jsonb (verified in prod: public.queries and troc.queries, 2026-10-20)
```

### Does NOT Exist
- ~~`DefinitionRepository.schedule_state` / `set_schedule_paused`~~: created here.
- ~~A `paused` column~~: the pause lives inside `attributes.scheduler`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/repositories/definitions.py", "action": "MODIFY"},
    {"path": "tests/scheduler/test_durable_pause_repository.py", "action": "CREATE"}
  ],
  "contract_symbols": ["sym:querysource/repositories/definitions.py#DefinitionRepository"]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Insert `schedule_state` after `exists`. *Why:* the fire path must read presence and
   pause in one round-trip.
2. Insert `set_schedule_paused` after `schedule_state`. Build the new `scheduler`
   object with `jsonb_set` on `COALESCE(attributes, '{}')`, and handle a missing
   `scheduler` key with `COALESCE(attributes->'scheduler', '{}'::jsonb) || $patch`.
   *Why:* atomic, with no read-modify-write race.
3. In `patch`, before building the SET clause, if `"attributes" in data`, merge the
   stored `scheduler.paused*` keys. *Why:* spec AC "survives an unrelated slug edit".

### `querysource/repositories/definitions.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    async def exists(self, store: QueryStore, slug: str) -> bool:' querysource/repositories/definitions.py)
# AFTER the `exists` method body (verified: querysource/repositories/definitions.py:188-192)
    async def schedule_state(self, store: QueryStore, slug: str) -> tuple[bool, bool]:
        """(present, paused) for ``slug`` in ``store``; replaces exists() on the scheduler fire path."""
        sql = (
            f"SELECT COALESCE((attributes->'scheduler'->>'paused')::boolean, false) AS paused "
            f"FROM {self._qualified_table(store)} WHERE query_slug = $1"
        )
        async with await self.connection_factory() as conn:
            row = await conn.fetch_one(sql, slug)
        if row is None:
            return False, False
        return True, bool(row["paused"])

    async def set_schedule_paused(self, store: QueryStore, slug: str, *, paused: bool,
                                  user: str | None, reason: str | None = None) -> bool:
        """Atomically set attributes.scheduler.paused (+ by/at/reason); False when the slug is missing."""
        # FILL IN: UPDATE {table} SET attributes = jsonb_set(COALESCE(attributes,'{}'::jsonb), '{scheduler}',
        #   COALESCE(attributes->'scheduler','{}'::jsonb) || $1::jsonb, true) WHERE query_slug = $2 RETURNING 1
        #   with $1 = json.dumps({"paused": paused, "paused_by": user, "paused_at": <utc iso>, "pause_reason": reason});
        #   on resume keep paused=false and clear pause_reason; translate errors via self._translate_write_error
        raise NotImplementedError
```
**Why**: the column is `jsonb` and `||` merges keys. Do not set `updated_at`: pausing is
an operational toggle, not a definition edit.

```python
# occurrences: 1 (verified: grep -c '    async def patch(' querysource/repositories/definitions.py)
# INSIDE `patch` (verified: querysource/repositories/definitions.py:518), after the `unknown` fields check
# and before `# Build the SET clause with only the provided fields`:
        if "attributes" in data:
            data = await self._preserve_schedule_pause(store, identity.slug, data)
```
And add the helper method:
```python
    async def _preserve_schedule_pause(self, store: QueryStore, slug: str,
                                       data: Mapping[str, Any]) -> dict[str, Any]:
        """Carry stored attributes.scheduler.paused* keys into an incoming attributes that omits them."""
        # FILL IN: fetch current attributes (self._fetch_row); incoming may be str JSON or dict — normalize;
        #   copy keys paused/paused_by/paused_at/pause_reason when absent in incoming["scheduler"];
        #   return a new dict (do not mutate the caller's mapping)
        raise NotImplementedError
```

### FILL IN checklist
- [ ] `set_schedule_paused` SQL + resume semantics + error translation.
- [ ] `_preserve_schedule_pause` normalization (str/dict) and key carry-over.
- [ ] Tests (below).

---

## Acceptance Criteria

- [ ] `schedule_state` returns `(False, False)` for a missing slug and reads `paused` correctly.
- [ ] `set_schedule_paused` changes only `scheduler.paused*` keys; other attributes intact.
- [ ] `patch` with `attributes` lacking `scheduler.paused` keeps the stored pause.
- [ ] Existing repository tests still pass.

---

## Validation Commands

- `pytest tests/scheduler/test_durable_pause_repository.py -q`
- `pytest tests/tenants/test_query_store_contract.py -q`

---

## Agent Instructions

1. Work in the feature worktree. This task has no dependencies.
2. Verify the anchors (counts = 1) before editing.
3. Set `in-progress`; implement; complete every `# FILL IN:`; run the Validation Commands.
4. Close with `scripts/sdd/close_task.sh TASK-933 scheduler-execution-history verified`.

---

## Completion Note

*(Agent fills this in when done)*
