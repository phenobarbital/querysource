# TASK-823: Source hooks core — `SourceHooks`, `pop_hooks`, `build_hooks`, `SourceHooksMixin`

**Feature**: FEAT-157 — MultiQuery Source Pre/Post-Hooks (PostgreSQL)
**Spec**: `sdd/specs/multi-source-hooks.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 1 (and §2 "Configuration", "Mixin on both parents"). This is the root
task of the feature. It creates the hook data model, the raw-key parsing helpers and
the `SourceHooksMixin`. TASK-824 puts the mixin on `BaseProvider` / `ThreadSource`, and
TASK-825 wires it into `MultiQS`.

> **CROSS-FEATURE BLOCKER — DO NOT START THIS FEATURE UNTIL FEAT-156
> (`multi-executesql`) IS MERGED INTO `dev`.**
> This module imports `querysource.interfaces.guarded_sql` (`GuardedSQLError`,
> `guard_statements`, `execute_guarded`). FEAT-156 creates that module (its task
> TASK-820), and it does not exist on `dev` at `f8a32ae`. Without it,
> `querysource/interfaces/source_hooks.py` cannot be imported at all. So the tests
> below, even though they mock the two functions, cannot collect before the merge.
> Before writing code, confirm that `querysource/interfaces/guarded_sql.py` exists
> on the feature worktree's base and that its signatures match the Codebase
> Contract below.

---

## Scope

- Create `querysource/interfaces/source_hooks.py` with these parts:
  - `HOOK_KEYS = ("pre-hook", "post-hook")`.
  - The frozen dataclass `SourceHooks(pre: tuple[str, ...] = (), post: tuple[str, ...] = ())`.
  - `pop_hooks(entry)`, which mutates `entry` and returns `(pre_raw, post_raw)`.
  - `build_hooks(pre, post)`. It calls `guard_statements` on each value that is present
    and returns `None` when both are absent. `GuardedSQLError` propagates.
  - `SourceHooksMixin` with a class-level `sql_hooks_dialect = None`, a class-level
    `_source_hooks = None`, and `set_hooks`, `has_hooks` (property), `run_pre_hook`
    and `run_post_hook`, which call `execute_guarded`.
- Create `tests/test_source_hooks.py`. Mock `guard_statements` / `execute_guarded`
  in the `querysource.interfaces.source_hooks` namespace.

**NOT in scope**:
- Inheriting the mixin anywhere, and the `_fetch_with_hooks` wrap (TASK-824).
- MultiQS validation, PBAC gate and dispatch wiring (TASK-825).
- Catalog docs (TASK-826).
- Any change to `guarded_sql.py` (FEAT-156 owns it).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/interfaces/source_hooks.py` | CREATE | Hook model, key parsing, `SourceHooksMixin` |
| `tests/test_source_hooks.py` | CREATE | Unit tests (guard/executor mocked) |

---

## Codebase Contract (Anti-Hallucination)

Re-verified against `dev` @ `f8a32ae`. `git diff 7337dbb f8a32ae -- querysource tests generated`
is empty, so there is no code drift since the spec was verified.

### Verified Imports
```python
from dataclasses import dataclass                                   # stdlib
from typing import ClassVar, List, Optional, Tuple, Union           # stdlib
import logging                                                      # stdlib (module logger; the mixin has no self.logger guarantee)
# From FEAT-156 — MUST exist on the base before starting (does NOT exist at f8a32ae):
from querysource.interfaces.guarded_sql import GuardedSQLError, execute_guarded, guard_statements
```
`querysource/interfaces/__init__.py` is empty (0 bytes), so importing a sibling module
does not trigger `interfaces.connections` → `providers`, and there is no import cycle
when TASK-824 imports this module from `providers/abstract.py`. FEAT-156's contract says
`guarded_sql.py` only imports `asyncdb`, `querysource.conf`, `querysource.exceptions` and
`querysource.qs_parsers`. **Re-check that after the merge.** If it imports anything under
`querysource.providers` or `querysource.queries`, STOP and report, because TASK-824 would
create a cycle.

### Existing Signatures to Use
```python
# querysource/interfaces/guarded_sql.py  (FEAT-156 spec §3 Module 2b — verify after merge)
class GuardedSQLError(QueryException):
    category: str
    def __init__(self, message: str, *, category: str) -> None: ...
def guard_statements(sql: Union[str, List[str]]) -> List[str]: ...
    # empty input / non-str items → GuardedSQLError("data"); HAS_RUST False → ("infra");
    # blocked or unparsable statement → ("data")
async def execute_guarded(statements: List[str], *, timeout: float = 3600.0) -> List[str]: ...
    # ONE transaction on default_dsn (DB*); returns status tags; any DB error → GuardedSQLError("infra")

# querysource/exceptions.py:6
class QueryException(Exception):
    def __init__(self, message: str, code: int | None = None, **kwargs): ...   # GuardedSQLError's base
```

### Does NOT Exist
- ~~`querysource.interfaces.source_hooks`~~: this task creates it.
- ~~`querysource.interfaces.guarded_sql`~~ on `dev` @ `f8a32ae`: FEAT-156 creates it. Merge FEAT-156 first.
- ~~`sql_hooks_dialect`, `set_hooks`, `has_hooks`, `run_pre_hook`, `run_post_hook`, `_source_hooks`~~
  on any existing class (`grep -rn` over `querysource/` and `tests/` returns nothing).
- ~~Hook fields on `QueryModel`~~: hooks never come from slug definitions.
- ~~A shared connection or transaction between hooks and the retrieval~~ (Option A): out of scope.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/interfaces/source_hooks.py", "action": "CREATE"},
    {"path": "tests/test_source_hooks.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/exceptions.py#QueryException"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **No `__init__` on the mixin.** `BaseProvider` and `ThreadSource` each have their own
  `__init__`, and `ThreadSource.__init__` calls `super().__init__()`, which must still
  reach `threading.Thread.__init__`. Per-instance state is created lazily by
  `set_hooks`. The class attribute `_source_hooks = None` is the default.
- `SourceHooks` holds **already guard-approved** statements, as tuples so the frozen
  dataclass stays hashable and immutable.
- `build_hooks` guards **both** values before returning. Nothing is stored or executed
  when either value is rejected (fail closed).
- The mixin never logs hook SQL at INFO or above. `run_*_hook` returns the status tags,
  and the caller (TASK-824 `_fetch_with_hooks`) logs them with the source name.
- Async throughout. Never block, since `execute_guarded` is awaited.

---

## Implementation Blueprint

### Steps (in order)
1. Verify that FEAT-156 is merged: `test -f querysource/interfaces/guarded_sql.py` and that the three
   names in the contract have those signatures. *Why*: the module imports them at top level.
2. Write `source_hooks.py` from the block below. *Why*: it fixes the public contract that TASK-824/825 consume.
3. Write `tests/test_source_hooks.py` and patch the names **in the `source_hooks` namespace**.
   *Why*: `from … import` binds the names locally, so patching `guarded_sql.*` would not take effect.
4. Run the Validation Commands and `ruff check` on both files.

### `querysource/interfaces/source_hooks.py` (CREATE)
```python
"""Pre/post SQL hooks for MultiQuery sources (FEAT-157).

Hooks are declared only on a MultiQuery ``queries`` entry (``pre-hook`` /
``post-hook``), guarded by the FEAT-156 Rust SQL guard and executed isolated on
the ``DB*`` credentials by :func:`execute_guarded` — never on the retrieval
connection, never in a transaction shared with it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar, List, Optional, Tuple, Union

from querysource.interfaces.guarded_sql import (  # FEAT-156
    GuardedSQLError,
    execute_guarded,
    guard_statements,
)

__all__ = (
    "HOOK_KEYS",
    "GuardedSQLError",
    "SourceHooks",
    "SourceHooksMixin",
    "build_hooks",
    "pop_hooks",
)

_log = logging.getLogger(__name__)

HOOK_KEYS: Tuple[str, str] = ("pre-hook", "post-hook")

RawHook = Optional[Union[str, List[str]]]


@dataclass(frozen=True)
class SourceHooks:
    """Guard-approved hook statements of one source."""

    pre: Tuple[str, ...] = ()
    post: Tuple[str, ...] = ()


def pop_hooks(entry: dict) -> Tuple[RawHook, RawHook]:
    """Remove and return the raw ``pre-hook`` / ``post-hook`` values of ``entry``.

    Args:
        entry: A MultiQuery ``queries`` entry. It is mutated: both keys are removed.

    Returns:
        ``(pre, post)``; each is ``None`` when the key was absent.
    """
    return entry.pop(HOOK_KEYS[0], None), entry.pop(HOOK_KEYS[1], None)


def build_hooks(pre: RawHook, post: RawHook) -> Optional[SourceHooks]:
    """Guard both raw values with ``guard_statements``.

    Args:
        pre: Raw ``pre-hook`` value (str, list of str, or None).
        post: Raw ``post-hook`` value (str, list of str, or None).

    Returns:
        ``None`` when both are absent, else the approved :class:`SourceHooks`.

    Raises:
        GuardedSQLError: blocked/unparsable/empty SQL or Rust extension missing.
    """
    if pre is None and post is None:
        return None
    # FILL IN: guard each present value (pre first, then post) and build the
    #   dataclass — bounded by: tuple(guard_statements(v)) for a present value,
    #   () for an absent one; never swallow GuardedSQLError; no DB access here.
    raise NotImplementedError


class SourceHooksMixin:
    """Pre/post SQL hooks capability shared by ``BaseProvider`` and ``ThreadSource``.

    Hooks execute isolated on ``DB*`` credentials (``execute_guarded``), never
    on the retrieval connection. Defines no ``__init__`` (cooperative MRO).
    """

    #: ``"postgres"`` on providers whose sources may declare hooks; ``None`` elsewhere.
    sql_hooks_dialect: ClassVar[Optional[str]] = None
    _source_hooks: Optional[SourceHooks] = None

    def set_hooks(self, hooks: Optional[SourceHooks]) -> None:
        """Attach validated hooks (``None`` clears them)."""
        self._source_hooks = hooks

    @property
    def has_hooks(self) -> bool:
        """True when a pre- or post-hook is attached."""
        hooks = self._source_hooks
        return hooks is not None and bool(hooks.pre or hooks.post)

    async def run_pre_hook(self) -> List[str]:
        """Execute the pre-hook statements in one transaction.

        Returns:
            The status tags (``[]`` when no pre-hook is attached).

        Raises:
            GuardedSQLError: any connection/statement failure (rolled back).
        """
        # FILL IN: [] when no hooks or no pre statements; else
        #   await execute_guarded(list(self._source_hooks.pre)) — bounded by: no
        #   timeout override (executor default), debug-level log only, no SQL at INFO+.
        raise NotImplementedError

    async def run_post_hook(self) -> List[str]:
        """Execute the post-hook statements in one transaction.

        Returns:
            The status tags (``[]`` when no post-hook is attached).

        Raises:
            GuardedSQLError: any connection/statement failure (rolled back).
        """
        # FILL IN: mirror run_pre_hook over self._source_hooks.post — bounded by
        #   the same rules as run_pre_hook.
        raise NotImplementedError
```
**Why this shape**: spec §2 "Mixin on both parents" and the §3 M1 skeleton. The class-level
`_source_hooks = None` and the missing `__init__` keep `ThreadSource.__init__` →
`super().__init__()` → `Thread.__init__` intact once TASK-824 puts the mixin first in
the bases. `GuardedSQLError` is re-exported so TASK-825 can import it from here. Do not
rename or re-type any public name.

### `tests/test_source_hooks.py` (CREATE)
```python
"""Unit tests for querysource.interfaces.source_hooks (FEAT-157, TASK-823)."""
from unittest.mock import AsyncMock, patch

import pytest

from querysource.interfaces import source_hooks
from querysource.interfaces.source_hooks import (
    HOOK_KEYS,
    GuardedSQLError,
    SourceHooks,
    SourceHooksMixin,
    build_hooks,
    pop_hooks,
)


class _Holder(SourceHooksMixin):
    """Bare mixin host (no __init__ of its own)."""


def test_pop_hooks_mutates_entry():
    entry = {"slug": "s", "pre-hook": "UPDATE t SET a = 1", "post-hook": ["INSERT INTO l VALUES (1)"], "x": 1}
    pre, post = pop_hooks(entry)
    assert pre == "UPDATE t SET a = 1"
    assert post == ["INSERT INTO l VALUES (1)"]
    assert entry == {"slug": "s", "x": 1}
    assert HOOK_KEYS == ("pre-hook", "post-hook")


def test_build_hooks_none_when_absent():
    with patch.object(source_hooks, "guard_statements") as guard:
        assert build_hooks(None, None) is None
    guard.assert_not_called()


def test_build_hooks_guarded():
    # FILL IN: (a) guard side_effect GuardedSQLError("statement 1: drop is not allowed", category="data")
    #   for "DROP TABLE x" → build_hooks raises GuardedSQLError; (b) guard returning a flattened list
    #   for a list of two scripts → SourceHooks.pre is that tuple; post-only → pre == ().
    pass


async def test_mixin_runs_execute_guarded():
    holder = _Holder()
    assert holder.has_hooks is False
    with patch.object(source_hooks, "execute_guarded", AsyncMock(return_value=["UPDATE 3"])) as ex:
        assert await holder.run_pre_hook() == []
        assert await holder.run_post_hook() == []
        ex.assert_not_awaited()
        holder.set_hooks(SourceHooks(pre=("UPDATE t SET a = 1",), post=("INSERT INTO l VALUES (1)",)))
        assert holder.has_hooks is True
        # FILL IN: assert run_pre_hook() == ["UPDATE 3"] and awaited with ["UPDATE t SET a = 1"];
        #   same for run_post_hook with the post statements; set_hooks(None) → has_hooks False.


def test_mixin_defaults_are_class_level():
    assert SourceHooksMixin.sql_hooks_dialect is None
    assert "__init__" not in SourceHooksMixin.__dict__
```

### FILL IN checklist
- [ ] `source_hooks.py::build_hooks`: guard `pre` then `post` and return `SourceHooks(tuple, tuple)`, bounded by: fail closed, no DB.
- [ ] `source_hooks.py::SourceHooksMixin.run_pre_hook` / `run_post_hook`: `[]` when nothing is attached, else `await execute_guarded(list(...))`, bounded by: no SQL logged at INFO or above.
- [ ] `tests/test_source_hooks.py`: complete `test_build_hooks_guarded` and the rest of `test_mixin_runs_execute_guarded`.

---

## Acceptance Criteria

- [ ] `from querysource.interfaces.source_hooks import HOOK_KEYS, SourceHooks, SourceHooksMixin, build_hooks, pop_hooks, GuardedSQLError` works (after FEAT-156 is merged).
- [ ] `pop_hooks` removes both keys and returns the raw values. `build_hooks(None, None) is None` without calling the guard.
- [ ] A guard rejection propagates as `GuardedSQLError`. A list of scripts is flattened into `SourceHooks.pre` / `.post`.
- [ ] `run_pre_hook` / `run_post_hook` await `execute_guarded` with exactly the stored statements, and return `[]` without awaiting when nothing is attached.
- [ ] `SourceHooksMixin` defines no `__init__`. `sql_hooks_dialect` and `_source_hooks` default to `None` at class level.
- [ ] `ruff check querysource/interfaces/source_hooks.py tests/test_source_hooks.py` is clean.

## Validation Commands

- `pytest tests/test_source_hooks.py -q`

---

## Test Specification

See the `tests/test_source_hooks.py` block above (spec §4 M1 rows: `test_pop_hooks_mutates_entry`,
`test_build_hooks_none_when_absent`, `test_build_hooks_guarded`, `test_mixin_runs_execute_guarded`).

---

## Agent Instructions

1. **Precondition**: FEAT-156 (`multi-executesql`) is merged into `dev`, and `querysource/interfaces/guarded_sql.py` exists there. If it is not, STOP and report. Do not stub `guarded_sql`.
2. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug multi-source-hooks --feature-id FEAT-157`).
3. Read the spec. This task has no `Depends-on` inside `sdd/tasks/index/multi-source-hooks.json`.
4. Verify the Codebase Contract, especially the FEAT-156 signatures, before writing code.
5. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
6. Run the Validation Commands. Commit only the listed files.
7. Close with `scripts/sdd/close_task.sh TASK-823 multi-source-hooks verified`, then fill in the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (sequential fallback, Claude)
**Date**: 2026-09-30
**Notes**: Created source_hooks.py (HOOK_KEYS, SourceHooks, pop_hooks, build_hooks, SourceHooksMixin) and tests/test_source_hooks.py. 6 tests pass, ruff clean. guarded_sql imports verified to not touch providers/queries (no cycle).

**Deviations from spec**: none
