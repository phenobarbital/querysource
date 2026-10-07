# TASK-854: docs/FILTER_OPERATORS.md + version bump to 5.2.2 (M9)

**Feature**: FEAT-180 — Partial-matching operators for where_cond / filter dict values
**Spec**: `sdd/specs/filter-with-partial-matching.spec.md`
**Status**: pending
**Priority**: low
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-840
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 9, AC12. Users need one page describing the `where_cond`/`filter`
partial-matching operators: the 20 names, per-dialect rendering (spec §2 rendered-forms
table), the reserved-name precedence over JSONB containment (PostgreSQL) and
`JSON_VALUE` extraction (BigQuery), escaping rules (`ESCAPE '!'` on generic SQL / SQL
Server, backslash on PostgreSQL / BigQuery), the error messages (from TASK-840's
`validate_partial_match` docstring) and examples. The qsurl page is updated by TASK-853.

---

## Scope

- Create `docs/FILTER_OPERATORS.md`.
- Create `tests/test_filter_operators_docs.py`.
- Bump `querysource/version.py` from `5.2.1` to `5.2.2`.

**NOT in scope**: `docs/QSURL.md` (TASK-853); README changes.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `docs/FILTER_OPERATORS.md` | CREATE | operator reference |
| `tests/test_filter_operators_docs.py` | CREATE | every operator documented |
| `querysource/version.py` | MODIFY | 5.2.1 → 5.2.2 |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase
> (re-verified on `dev` at `71316ad4`, 2026-10-07). The implementing agent MUST use these exact
> imports, class names, and method signatures. **DO NOT** invent, guess, or assume any import,
> attribute, or method not listed here. If you need something not listed, VERIFY it exists first.

### Verified Imports
```python
from querysource.parsers.partial_matching import PARTIAL_MATCH_OPERATORS   # TASK-840
```

### Existing Signatures to Use
```python
# querysource/version.py:9
__version__ = '5.2.1'
# docs/ precedent: docs/JSONB_AGGREGATION.md documents a where_cond feature with
# `SELECT * FROM students {where_cond}` style examples (line 38)
```

### Does NOT Exist
- ~~`docs/FILTER_OPERATORS.md`~~ — created by THIS task.

---

## Complexity Contract

> Declaration for deterministic complexity routing (FEAT-561). `targets` match the
> Files table exactly.

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "docs/FILTER_OPERATORS.md",
      "action": "CREATE"
    },
    {
      "path": "tests/test_filter_operators_docs.py",
      "action": "CREATE"
    },
    {
      "path": "querysource/version.py",
      "action": "MODIFY"
    }
  ],
  "contract_symbols": [
    "sym:querysource/parsers/partial_matching.py#PARTIAL_MATCH_OPERATORS"
  ]
}
```

---

## Implementation Blueprint

> **CRITICAL — Executor-ready starting point.** Write each block below to its declared
> path nearly verbatim, then complete every `# FILL IN:` marker. Blocks were derived
> from the spec's Interface Skeletons and re-verified against the Codebase Contract
> above when this task was written. Never change a signature, class name, or file path
> the blueprint fixes.

### Steps (in order)
1. Write the doc from spec §2 — *why*: the rendered-forms table is normative; copy it, do not re-derive.
2. Write the test; bump the version; run the Validation Command.

### `docs/FILTER_OPERATORS.md` (CREATE)
```markdown
# Partial-matching filter operators

`where_cond` / `filter` accept a dict value whose single key is a partial-matching operator:

    "where_cond": { "full_name": { "startswith": "andre" } }

## Operators
<!-- FILL IN: the 20 operators (table: operator, meaning, case, operand handling) — bounded by AC12 -->

## Rendering per dialect
<!-- FILL IN: spec §2 rendered-forms table, including ESCAPE '!' and the BigQuery backslash note -->

## Reserved names
<!-- FILL IN: precedence over PostgreSQL JSONB containment and BigQuery JSON_VALUE extraction;
     alternatives (->>, @>, explicit JSON_VALUE field) -->

## Errors
<!-- FILL IN: the ParserError messages (HTTP 400) from validate_partial_match, including
     "one operator per field" and the contains minimum length of 3 -->

## qsurl
<!-- FILL IN: one paragraph linking docs/QSURL.md (`~ ~* ^= ^=* $= $=* =~ =~*`) -->
```

### `querysource/version.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "__version__ = '5.2.1'" querysource/version.py) — line 9
# REPLACE line 9 with:
__version__ = '5.2.2'
```

### `tests/test_filter_operators_docs.py` (CREATE)
```python
"""FEAT-180: docs/FILTER_OPERATORS.md documents every partial-matching operator (spec AC12)."""
from pathlib import Path

import pytest

from querysource.parsers.partial_matching import PARTIAL_MATCH_OPERATORS

DOC = Path(__file__).resolve().parents[1] / "docs" / "FILTER_OPERATORS.md"
HEADINGS = ("## Operators", "## Rendering per dialect", "## Reserved names", "## Errors", "## qsurl")


def test_headings():
    text = DOC.read_text(encoding="utf-8")
    assert [h for h in HEADINGS if h not in text] == []


@pytest.mark.parametrize("op", list(PARTIAL_MATCH_OPERATORS))
def test_operator_documented(op):
    assert f"`{op}`" in DOC.read_text(encoding="utf-8")
```

### FILL IN checklist
- [ ] The five doc sections — bounded by AC12.

---

## Acceptance Criteria

- [ ] AC12: every operator name appears in backticks; all five headings exist; examples use the spec §2 strings.
- [ ] `querysource/version.py` reads `5.2.2`.

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/test_filter_operators_docs.py -q`

---

## Test Specification

See the `tests/test_filter_operators_docs.py` block above.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug filter-with-partial-matching --feature-id FEAT-180`)
2. **Read the spec** at the path listed above for full context (§2 rendered-forms table is normative)
3. **Check dependencies** — every `Depends-on` task must be `"done"` in the
   per-spec index `sdd/tasks/index/filter-with-partial-matching.json`
4. **Verify the Codebase Contract** — before writing ANY code:
   - Confirm every import in "Verified Imports" still exists (`grep` or `read` the source)
   - Re-run `grep -c` for every MODIFY anchor in the blueprint; a count that differs from the
     one recorded means the anchor moved — re-locate it; a count of `0` means STOP and report drift
   - **NEVER** reference an import, attribute, or method not in the contract without verifying it exists
5. **Update status** in `sdd/tasks/index/filter-with-partial-matching.json` → `"in-progress"`
   (set `started_at`) and commit only that index file
6. **Implement** — start from the Implementation Blueprint blocks, complete every
   `# FILL IN:` marker, and never change a signature or path the blueprint fixes
7. **Verify** all acceptance criteria are met — run the Validation Commands
8. **Commit the code** — stage only the files this task lists (never `git add .` / `-A`)
9. **Close the task** with `scripts/sdd/close_task.sh TASK-854 filter-with-partial-matching verified`
   — it moves this file to `sdd/tasks/completed/` and marks it `"done"` in the
   index; never move or copy the file by hand
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
