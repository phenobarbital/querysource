# TASK-941: Document scheduler run history, run now and durable pause

**Feature**: FEAT-181 — Scheduler Execution History
**Spec**: `sdd/specs/scheduler-execution-history.spec.md`
**Status**: pending
**Priority**: low
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-939
**Assigned-to**: unassigned

---

## Context

Spec §5 AC + §7. Operators and frontend developers need one place that explains the
history store (DocumentDB, TTL), the fire-and-forget guarantee, single-execution,
run now, durable pause, alerts and the interval anchor. The anchor shifts the phase
of existing interval jobs, so that must be called out.

---

## Scope

- Append a "Run history (FEAT-181)" section to `docs/QSSCHEDULER.md` covering:
  - storage and TTL;
  - config keys;
  - the `scheduler-history` extra;
  - fire-and-forget and fail-open;
  - single-execution;
  - endpoints with examples;
  - durable pause in `attributes.scheduler.paused`;
  - alerts;
  - interval anchor (phase change);
  - DocumentDB TTL approximation.
- Add a doc test that the section exists.

**NOT in scope**: code.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `docs/QSSCHEDULER.md` | MODIFY | New section |
| `tests/scheduler/test_history_docs.py` | CREATE | Section-present test |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from pathlib import Path   # stdlib
```

### Existing Signatures to Use
- `docs/QSSCHEDULER.md` exists (FEAT-100 docs).
- Endpoints and config keys: spec §2 / §6 (source of truth; copy, do not invent).

### Does NOT Exist
- ~~Postgres history tables~~: the docs must state that history is DocumentDB only.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "docs/QSSCHEDULER.md", "action": "MODIFY"},
    {"path": "tests/scheduler/test_history_docs.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Append the section. Take every endpoint and key verbatim from the spec.
2. Write the test.

### `docs/QSSCHEDULER.md` (MODIFY)
```markdown
<!-- AFTER — append at end of file -->
## Run history (FEAT-181)
<!-- FILL IN: subsections listed in Scope; endpoint examples with real response shapes from spec §2 -->
```

### `tests/scheduler/test_history_docs.py` (CREATE)
```python
"""FEAT-181: QSSCHEDULER.md documents run history."""
from pathlib import Path


def test_history_section_present() -> None:
    text = Path("docs/QSSCHEDULER.md").read_text(encoding="utf-8")
    assert "## Run history (FEAT-181)" in text
    assert "QS_SCHEDULER_HISTORY_TTL_DAYS" in text
```

### FILL IN checklist
- [ ] All Scope subsections present and accurate.

---

## Acceptance Criteria

- [ ] Section covers every Scope item and matches spec §2/§6.

---

## Validation Commands

- `pytest tests/scheduler/test_history_docs.py -q`

---

## Agent Instructions

1. Work in the feature worktree; check TASK-939 is done.
2. Close with `scripts/sdd/close_task.sh TASK-941 scheduler-execution-history verified`.

---

## Completion Note

*(Agent fills this in when done)*
