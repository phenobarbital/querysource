# TASK-828: Write-capability predicates for scheduled multi-queries

**Feature**: FEAT-160 — Scheduler Admin Gate for Write-Capable Multi-Queries
**Spec**: `sdd/specs/multi-scheduler-admin-gate.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §2 M2 and §3 Module 2. Both schedule write points (TASK-829 slug management, TASK-830 scheduler
sync) need a single decision: *is this definition a scheduled MultiQuery that can write with the DB\*
credentials?* A pipeline is write-capable when its `Output` uses a `WRITE_DESTINATIONS` step (FEAT-155,
already on `dev`) or when any `queries` entry declares a `pre-hook` / `post-hook` (FEAT-157). This task
adds pure, synchronous predicates next to `_output_step_names` in `querysource/queries/multi/__init__.py`.

`WRITE_DESTINATIONS` is a module global read at **call time**, so FEAT-156's `"ExecuteSQL"` (TASK-822
replaces the frozenset literal) is picked up without touching this code. `SOURCE_HOOK_KEYS` duplicates
FEAT-157's `HOOK_KEYS` (`querysource/interfaces/source_hooks.py`, TASK-823, not on `dev`). Whichever
feature merges second makes one module import the other (spec §7).

---

## Scope

- Add `SOURCE_HOOK_KEYS`, a private `_as_mapping`, `pipeline_requires_write_grant` and
  `definition_requires_scheduler_grant` between `_output_step_names` and `get_operator_module`.
- Add three stdlib imports: `json`, `collections.abc.Mapping`, and `Any` in the existing `typing` import.
- Create `tests/test_scheduler_write_predicates.py`.

**NOT in scope**: changing `WRITE_DESTINATIONS` or `_output_step_names`, and any change to
`MultiQS._preflight_principal`. Also out of scope: importing FEAT-157's `HOOK_KEYS` (not on `dev`) and
any caller (TASK-829 / TASK-830).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/__init__.py` | MODIFY | imports + `SOURCE_HOOK_KEYS`, `_as_mapping`, `pipeline_requires_write_grant`, `definition_requires_scheduler_grant` |
| `tests/test_scheduler_write_predicates.py` | CREATE | predicate unit tests |

---

## Codebase Contract (Anti-Hallucination)

Re-verified against HEAD `71ebae0`. Line numbers are unchanged from the spec (`c26af0c`).

### Verified Imports
```python
from querysource.queries.multi import WRITE_DESTINATIONS, _output_step_names   # verified: querysource/queries/multi/__init__.py:72,75
import querysource.queries.multi as multi                                      # monkeypatch target for WRITE_DESTINATIONS in tests
```

### Existing Signatures to Use
```python
# querysource/queries/multi/__init__.py
import asyncio                                                    # line 1
from importlib import import_module                               # line 3
from typing import TYPE_CHECKING, Optional                        # line 4 — becomes TYPE_CHECKING, Any, Optional
WRITE_DESTINATIONS: frozenset[str] = frozenset({"TableDelete"})   # line 72 (FEAT-156 TASK-822 adds "ExecuteSQL")
def _output_step_names(output: object) -> set[str]:               # line 75 — list/tuple of {name: cfg}; else set()
def get_operator_module(clsname: str):                            # line 95 — insertion anchor (insert ABOVE it)

# querysource/scheduler/scheduler.py:327-331 — how QSScheduler parses query_raw (the rule to mirror):
#   raw = row.get("query_raw") or ""
#   payload = json.loads(raw) if isinstance(raw, str) and raw.strip() else None   (JSONDecodeError → None)
# querysource/scheduler/scheduler.py:300-302 — attributes = row.get("attributes") or {}; scheduler_def = attributes.get("scheduler"); falsy → no job
# querysource/models.py:55 attributes: Optional[dict]; :78 query_raw: str; :81 provider default 'db'
```

### Does NOT Exist
- ~~`SOURCE_HOOK_KEYS`, `_as_mapping`, `pipeline_requires_write_grant`, `definition_requires_scheduler_grant`~~: created by this task.
- ~~`HOOK_KEYS` in `querysource.interfaces.source_hooks` on `dev`~~: FEAT-157 is not merged. Do not import it.
- ~~`import json` / `Mapping` / `Any` in `querysource/queries/multi/__init__.py`~~: not imported today, so add them.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/__init__.py", "action": "MODIFY"},
    {"path": "tests/test_scheduler_write_predicates.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/__init__.py#WRITE_DESTINATIONS",
    "sym:querysource/queries/multi/__init__.py#_output_step_names",
    "sym:querysource/queries/multi/__init__.py#get_operator_module"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- The predicates are pure and synchronous, and they never raise on malformed input: any unexpected shape returns `False`.
- The `WRITE_DESTINATIONS` lookup must stay a module-global name lookup inside the function body.
  Do not bind it as a default argument or copy it into a closure.
- Hook detection is key *presence* on a `queries` entry, the same rule as TASK-825's
  `any(key in cfg for key in HOOK_KEYS)`. The value is not inspected.
- `query_raw` that is not a JSON object → not write-capable (MultiQS falls back to single-query mode,
  `scheduler.py:333-341`). A `query_raw` / `attributes` that is already a mapping is accepted as-is.
  This is defensive over-gating for callers that pass parsed payloads.

---

## Implementation Blueprint

### Steps (in order)
1. Extend the imports. *Why*: `json.loads`, `Mapping`, `Any` are used by the new helpers.
2. Insert the predicate block above `get_operator_module`. *Why*: next to `_output_step_names` / `WRITE_DESTINATIONS` (spec §3 M2).
3. Create the tests and run the Validation Commands plus `ruff check querysource/queries/multi/__init__.py tests/test_scheduler_write_predicates.py`.

### `querysource/queries/multi/__init__.py` (MODIFY — imports)
```python
# occurrences: 1 (verified: grep -c '^import asyncio$' querysource/queries/multi/__init__.py)
# AFTER — insert below `import asyncio` (verified: querysource/queries/multi/__init__.py:1)
import json

# occurrences: 1 (verified: grep -c '^from importlib import import_module$' querysource/queries/multi/__init__.py)
# BEFORE — insert above `from importlib import import_module` (verified: querysource/queries/multi/__init__.py:3)
from collections.abc import Mapping

# occurrences: 1 (verified: grep -c '^from typing import TYPE_CHECKING, Optional$' querysource/queries/multi/__init__.py)
# REPLACE `from typing import TYPE_CHECKING, Optional` (verified: querysource/queries/multi/__init__.py:4) with:
from typing import TYPE_CHECKING, Any, Optional
```
**Why**: ruff `I` (isort) order is kept. The import order was checked with `ruff check` on a scratch copy.

### `querysource/queries/multi/__init__.py` (MODIFY — predicates)
```python
# occurrences: 1 (verified: grep -c '^def get_operator_module(clsname: str):$' querysource/queries/multi/__init__.py)
# BEFORE — insert above `def get_operator_module(clsname: str):` (verified: querysource/queries/multi/__init__.py:95),
# i.e. right after the body of `def _output_step_names(output: object) -> set[str]:` (line 75); keep two blank lines around it.
# FEAT-160: source-hook keys that run SQL with the full-access DB* connection.
# Same literals as FEAT-157's ``HOOK_KEYS`` (querysource/interfaces/source_hooks.py);
# whichever feature merges second makes one module import the other.
SOURCE_HOOK_KEYS: tuple[str, str] = ("pre-hook", "post-hook")


def _as_mapping(value: object) -> Mapping[str, Any] | None:
    """Return ``value`` as a mapping: a mapping as-is, a JSON-object string parsed.

    Args:
        value: A mapping, a JSON string, or anything else.

    Returns:
        The mapping, or ``None`` for malformed JSON, non-object JSON and other types.
    """
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def pipeline_requires_write_grant(pipeline: object) -> bool:
    """Tell whether a MultiQuery pipeline can write with the DB* credentials.

    Args:
        pipeline: The parsed MultiQuery payload (``{"queries": …, "Output": […]}``).

    Returns:
        True when ``Output`` uses a ``WRITE_DESTINATIONS`` step or any ``queries``
        entry declares a ``SOURCE_HOOK_KEYS`` key; False otherwise, including for
        malformed shapes.
    """
    if not isinstance(pipeline, Mapping):
        return False
    if _output_step_names(pipeline.get("Output")) & WRITE_DESTINATIONS:
        return True
    queries = pipeline.get("queries")
    if not isinstance(queries, Mapping):
        return False
    return any(
        isinstance(entry, Mapping) and any(key in entry for key in SOURCE_HOOK_KEYS)
        for entry in queries.values()
    )


def definition_requires_scheduler_grant(definition: Mapping[str, Any]) -> bool:
    """Tell whether saving/syncing this definition needs ``datasource:use`` on ``pg_admin``.

    Args:
        definition: A definition row or merged change with ``provider``,
            ``attributes`` (mapping or JSON string) and ``query_raw`` (JSON string
            or mapping).

    Returns:
        True only for a ``provider == "multi"`` definition with a truthy
        ``attributes.scheduler`` whose ``query_raw`` pipeline is write-capable.
        ``query_raw`` that is not a JSON object returns False, because MultiQS then
        falls back to single-query mode.
    """
    if not isinstance(definition, Mapping) or definition.get("provider") != "multi":
        return False
    attributes = _as_mapping(definition.get("attributes"))
    if not attributes or not attributes.get("scheduler"):
        return False
    return pipeline_requires_write_grant(_as_mapping(definition.get("query_raw")))
```
**Why**: this implements the spec §2 M2 rules exactly. The anchor is `get_operator_module` rather than the
end of `_output_step_names`, because a bare "after the function" anchor would be ambiguous.
**Cross-feature**: FEAT-156 TASK-822 rewrites line 72 and FEAT-157 TASK-825 edits `_preflight_principal`
and adds an import in this file. Neither touches lines 93-95, so a rebase should apply cleanly. After
FEAT-157 merges, replace the `SOURCE_HOOK_KEYS` literal with
`from ...interfaces.source_hooks import HOOK_KEYS as SOURCE_HOOK_KEYS` (spec §7). That is not this task,
unless FEAT-157 is already on `dev` when you start. In that case do it here and record it as a deviation.

### `tests/test_scheduler_write_predicates.py` (CREATE)
```python
"""FEAT-160 / TASK-828: write-capability predicates for scheduled multi-queries."""
import json

import pytest

import querysource.queries.multi as multi
from querysource.queries.multi import (
    SOURCE_HOOK_KEYS,
    definition_requires_scheduler_grant,
    pipeline_requires_write_grant,
)

READ_ONLY = {"queries": {"a": {"slug": "report_a"}}, "Output": [{"Table": {"table": "t"}}]}
DELETE = {"queries": {"a": {"slug": "report_a"}}, "Output": [{"TableDelete": {"table": "t"}}]}
HOOKED = {"queries": {"a": {"slug": "report_a", "post-hook": "UPDATE t SET x = 1"}}}
SCHEDULE = {"scheduler": {"schedule_type": "cron", "schedule": {"hour": 1}}}


def _definition(pipeline, *, provider="multi", attributes=SCHEDULE):
    raw = pipeline if isinstance(pipeline, str) else json.dumps(pipeline)
    return {"provider": provider, "query_raw": raw, "attributes": attributes}


def test_pipeline_requires_write_grant(monkeypatch):
    assert pipeline_requires_write_grant(DELETE) is True
    assert pipeline_requires_write_grant(HOOKED) is True
    assert pipeline_requires_write_grant(
        {"queries": {"a": {"slug": "s", "pre-hook": ["DELETE FROM t"]}}}
    ) is True
    assert pipeline_requires_write_grant(READ_ONLY) is False
    for malformed in (None, "x", [], {"queries": ["a"]}, {"queries": {"a": "slug"}}, {"Output": "Table"}):
        assert pipeline_requires_write_grant(malformed) is False
    assert SOURCE_HOOK_KEYS == ("pre-hook", "post-hook")
    # WRITE_DESTINATIONS is read at call time (FEAT-156 adds "ExecuteSQL").
    execute = {"queries": {}, "Output": [{"ExecuteSQL": {"sql": "x"}}]}
    monkeypatch.setattr(multi, "WRITE_DESTINATIONS", frozenset({"TableDelete", "ExecuteSQL"}))
    assert pipeline_requires_write_grant(execute) is True


@pytest.mark.parametrize(
    "definition",
    [
        _definition(DELETE, provider="db"),
        _definition(DELETE, attributes=None),
        _definition(DELETE, attributes={}),
        _definition(DELETE, attributes={"scheduler": {}}),
        _definition(READ_ONLY),
        _definition("SELECT 1"),
        _definition("{not json"),
        {"provider": "multi", "attributes": SCHEDULE},
        {},
    ],
)
def test_definition_requires_scheduler_grant_false(definition):
    assert definition_requires_scheduler_grant(definition) is False


def test_definition_requires_scheduler_grant_true():
    assert definition_requires_scheduler_grant(_definition(DELETE)) is True
    assert definition_requires_scheduler_grant(_definition(HOOKED)) is True
    # attributes stored as a JSON string, query_raw already parsed
    assert definition_requires_scheduler_grant(
        {"provider": "multi", "attributes": json.dumps(SCHEDULE), "query_raw": DELETE}
    ) is True
```

### FILL IN checklist
- [ ] None. Every block is complete. If FEAT-156 is already merged, the monkeypatch in
  `test_pipeline_requires_write_grant` is redundant but still valid.

---

## Acceptance Criteria

- [ ] `from querysource.queries.multi import SOURCE_HOOK_KEYS, pipeline_requires_write_grant, definition_requires_scheduler_grant` works.
- [ ] A `TableDelete` (or, once FEAT-156 merges, `ExecuteSQL`) Output step → True. A hook key on any `queries` entry → True. A `Table`-only Output → False. Malformed shapes → False.
- [ ] A definition that is not multi, has no or empty `attributes.scheduler`, has a read-only pipeline, or has non-JSON `query_raw` → False. A scheduled write multi → True.
- [ ] Existing multi tests still pass (`tests/test_multiqs_write_gate.py`, `tests/multi/test_multiqs_principal.py`).
- [ ] `ruff check querysource/queries/multi/__init__.py tests/test_scheduler_write_predicates.py` is clean.

## Validation Commands

- `python -m pytest tests/test_scheduler_write_predicates.py -q -p no:cacheprovider`
- `python -m pytest tests/test_multiqs_write_gate.py -q -p no:cacheprovider`
- `python -m pytest tests/multi/test_multiqs_principal.py -q -p no:cacheprovider`

---

## Test Specification

See the `tests/test_scheduler_write_predicates.py` block above. It covers spec §4 `test_pipeline_requires_write_grant` and `test_definition_requires_scheduler_grant`.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree**, never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug multi-scheduler-admin-gate --feature-id FEAT-160`).
   **Environment:** in a fresh worktree, first run `python setup.py build_ext --inplace`, because the Cython
   `.so` files are not versioned. Then run every test from the worktree root with the shared venv, as
   `python -m pytest <file> -q -p no:cacheprovider`.
2. **Read the spec** at the path listed above for full context.
3. **Check dependencies**: none for this task (`sdd/tasks/index/multi-scheduler-admin-gate.json`).
4. **Verify the Codebase Contract**: re-run every `grep -c` anchor. If a count is not 1, re-locate the anchor before editing.
5. **Update status** in `sdd/tasks/index/multi-scheduler-admin-gate.json` to `"in-progress"` (set `started_at`) and commit only that index file.
6. **Implement** from the blueprint. Never change a signature or path the blueprint fixes.
7. **Verify** by running the Validation Commands.
8. **Commit the code**, staging only the files this task lists (never `git add .` / `-A`).
9. **Close the task** with `scripts/sdd/close_task.sh TASK-828 multi-scheduler-admin-gate verified`.
10. **Fill in the Completion Note** below, then commit the staged SDD state.

---

## Completion Note

**Completed by**: sdd-worker (sequential fallback)
**Date**: 2026-09-30
**Notes**: Added SOURCE_HOOK_KEYS, _as_mapping, pipeline_requires_write_grant, definition_requires_scheduler_grant to queries/multi/__init__.py. 11 new tests plus existing multi tests pass; ruff clean.

**Deviations from spec**: none
