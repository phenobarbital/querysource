# AI-Parrot SDD Workflow for Claude Code

## Overview

This document defines the **Spec-Driven Development (SDD)** methodology for AI-Parrot,
the library consumed by Flowtask.

> **When to use this workflow:**
> Use this when a Flowtask feature requires changes **inside the parrot library** before
> Flowtask can consume them. Common triggers:
> - Adding a new `AbstractPlanogramType` subclass (e.g., `InkWall`, `BoxesOnFloor`)
> - Extending a parrot model (`PlanogramConfig`, `IdentifiedProduct`, etc.)
> - Adding a new pipeline, tool, or LLM client to parrot
> - Fixing a bug in parrot that blocks a Flowtask feature
>
> If your Flowtask feature only changes `.py` files inside `flowtask/`, use
> `sdd/WORKFLOW.md` instead.

---

## Cross-Project Development Order

When a feature spans both parrot and flowtask, **always develop in this order**:

```
1. parrot changes  →  spec + tasks in parrot repo  →  release / install
       ↓
2. flowtask changes  →  spec references parrot version  →  PR to main
```

The Flowtask spec must declare the parrot dependency explicitly in Section 4
(External Dependencies) and must not start implementation until the parrot
changes are available in the virtual environment.

---

## The SDD Lifecycle (parrot)

```
/sdd-proposal → discuss → /sdd-spec → /sdd-task → /sdd-start → implement → /sdd-done
                                ↑
                  (or /sdd-fromjira if ticket exists in Jira)
```

### Phase 0 — Feature Proposal *(optional)*
Use `/sdd-proposal` when the idea is not fully defined. The agent walks through
motivation, scope, and impact, producing `docs/sdd/proposals/<feature>.proposal.md`
inside the parrot repo.

### Phase 1 — Feature Specification
Use `/sdd-spec` to scaffold `docs/sdd/specs/<feature>.spec.md`. If a Jira ticket
exists, use `/sdd-fromjira` to bootstrap the spec from the ticket.

### Phase 2 — Task Generation
Run `/sdd-task <spec-file>` on the `dev` branch of the parrot repo. Tasks are
written to `tasks/active/TASK-<id>-<slug>.md`.

### Phase 3 — Task Execution
Each task runs in its own worktree branched from `dev` in the parrot repo:
```bash
git worktree add -b feat-<ID>-<slug> .worktrees/feat-<ID>-<slug> HEAD
```

### Phase 4 — Validation & Release
After all tasks are done, run `/sdd-done` to verify, push, and open a PR.
Once merged and a new parrot version is released, update the Flowtask venv:
```bash
source .venv/bin/activate
uv pip install --upgrade parrot
```

---

## Branch Strategy (parrot repo)

```
main ──────────────────────────── production (PR target)
  └── dev ─────────────────────── integration branch (SDD state lives here)
        ├── feat-001-ink-wall ──── feature worktree
        └── feat-002-boxes ─────── feature worktree
```

### `dev` Sync Policy
Same rule as Flowtask: **sync `dev` with `main` before creating any worktree.**

```bash
git checkout dev
git pull origin main --rebase
```

---

## Task Artifact Format

Every task file (`tasks/active/TASK-<NNN>-<slug>.md`) follows this structure:

```markdown
# TASK-<NNN>: <Title>

**Feature**: <parent feature name>
**Feature ID**: FEAT-<NNN>
**Spec**: docs/sdd/specs/<feature>.spec.md
**Status**: pending | in-progress | done
**Priority**: high | medium | low
**Effort**: S | M | L | XL
**Depends-on**: TASK-<X>, TASK-<Y>   (or "none")
**Assigned-to**: (agent session ID or "unassigned")

## Context
Why this task exists and how it fits the feature.

## Scope
Exactly what this task must implement.

## Files to Create/Modify
- `parrot/pipelines/<area>/<file>.py` — description
- `tests/<area>/test_<file>.py` — unit tests

## Implementation Notes
Patterns to follow, existing code to reference, gotchas, constraints.

## Reference Code
- See `parrot/pipelines/planogram/types/abstract.py` for AbstractPlanogramType pattern
- See `parrot/pipelines/planogram/types/product_on_shelves.py` for concrete type example

## Acceptance Criteria
- [ ] Criterion 1
- [ ] All tests pass: `pytest tests/ -v`

## Test Specification
# Minimal test scaffold the agent must make pass
def test_new_type_compute_roi():
    ...

### Completion Note
(Agent fills this in when done)
```

---

## Task Index Schema (`tasks/.index.json`)

```json
{
  "tasks": [
    {
      "id": "TASK-001",
      "slug": "ink-wall-type",
      "title": "Implement InkWall planogram type",
      "feature_id": "FEAT-001",
      "feature": "ink-wall-support",
      "spec": "docs/sdd/specs/ink-wall-support.spec.md",
      "status": "pending",
      "priority": "high",
      "effort": "M",
      "depends_on": [],
      "parallel": false,
      "parallelism_notes": "",
      "assigned_to": null,
      "started_at": null,
      "completed_at": null,
      "file": "tasks/active/TASK-001-ink-wall-type.md"
    }
  ]
}
```

---

## Parallelism Rules

Parrot tasks can run in parallel when they touch independent modules:

```
TASK-001 (AbstractPlanogramType extension)
    ├── TASK-002 (InkWall type)        ← parallel after 001
    └── TASK-003 (BoxesOnFloor type)   ← parallel after 001
            └── TASK-004 (tests)       ← waits for 002 and 003
```

---

## Commands Reference

| Command | When to use |
|---------|-------------|
| `/sdd-fromjira` | Bootstrap spec from a Jira ticket |
| `/sdd-tojira` | Export spec to a Jira story |
| `/sdd-proposal` | Discuss a feature before building a spec |
| `/sdd-brainstorm` | Explore technical options |
| `/sdd-spec` | Scaffold a feature spec |
| `/sdd-task <spec.md>` | Decompose spec into tasks |
| `/sdd-start <TASK-ID>` | Begin task implementation |
| `/sdd-status` | Show task board |
| `/sdd-next` | Suggest next unblocked tasks |
| `/sdd-codereview` | Review a completed task |
| `/sdd-done` | Verify, push, and cleanup |

---

## Quality Rules for Agents

1. **Never modify files outside the task scope** — respect boundaries
2. **Follow existing patterns** — reference code mentioned in the task
3. **async/await throughout** — no blocking I/O in async contexts
4. **Google-style docstrings** — all public functions and classes
5. **Type hints** — strict typing on all function signatures
6. **Pydantic models** — for all data structures
7. **pytest** — all tests use pytest, run with `pytest tests/ -v`
8. **Update the index** — always update `.index.json` on completion
9. **Small commits** — one task = one logical commit
