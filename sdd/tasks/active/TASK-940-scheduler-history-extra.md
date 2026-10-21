# TASK-940: `scheduler-history` optional dependency extra

**Feature**: FEAT-181 — Scheduler Execution History
**Spec**: `sdd/specs/scheduler-execution-history.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §7 "Dependency". DocumentDB access needs `motor`/`pymongo`, through asyncdb's
`mongodb` extra (pymongo 4.12.0, motor 3.7.0; already installed on prod
navigator-api). They are not in this repo's dependencies, and the local venv lacks
them. History degrades when they are missing, so the dependency is an **optional
extra**, not a core dependency.

---

## Scope

- Add `scheduler-history = ["asyncdb[mongodb]"]` under `[project.optional-dependencies]`.
- Re-lock with `uv lock`, and install it in the worktree venv so the integration test
  (TASK-942) can import motor.

**NOT in scope**: code changes.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `pyproject.toml` | MODIFY | New optional extra |
| `uv.lock` | MODIFY | Re-lock |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# none (packaging only)
```

### Existing Signatures to Use
```toml
# pyproject.toml:121
[project.optional-dependencies]
# extras style, e.g. line 154: onedrive = [ ... ]
# asyncdb 2.15.9 extra "mongodb" → pymongo==4.12.0, motor==3.7.0 (verified via importlib.metadata.requires("asyncdb"))
```

### Does NOT Exist
- ~~A `mongo` / `documentdb` extra in this pyproject~~: new here.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "pyproject.toml", "action": "MODIFY"},
    {"path": "uv.lock", "action": "MODIFY"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Add the extra, then run `uv lock`. *Why:* the lockfile must match. Run `uv lock`,
   not `pip`, per repo conventions.
2. `uv pip install -e '.[scheduler-history]'` in the worktree venv. *Why:* TASK-942
   imports motor.

### `pyproject.toml` (MODIFY)
```toml
# occurrences: 1 (verified: grep -c '\[project.optional-dependencies\]' pyproject.toml)
# AFTER `[project.optional-dependencies]` (verified: pyproject.toml:121)
# FEAT-181: DocumentDB run history for QSScheduler (motor/pymongo via asyncdb).
scheduler-history = ["asyncdb[mongodb]"]
```

### FILL IN checklist
- [ ] `uv lock` succeeds without loosening existing overrides (`[tool.uv] override-dependencies`).

---

## Acceptance Criteria

- [ ] `uv lock` is clean; `uv.lock` contains motor 3.7.0 and pymongo 4.12.0 under the extra.
- [ ] Core install unchanged (no new core dependency).

---

## Validation Commands

- `pytest tests/scheduler/test_parse_trigger_shorthands.py -q`

---

## Agent Instructions

1. Exclusive task: lockfile change, never parallel with others.
2. Work in the feature worktree; set `in-progress`; implement; validate.
3. Close with `scripts/sdd/close_task.sh TASK-940 scheduler-execution-history verified`.

---

## Completion Note

*(Agent fills this in when done)*
