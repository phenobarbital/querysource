# TASK-778: qsurl dialect reference (`docs/QSURL.md`)

**Feature**: FEAT-152 — qsurl: HTSQL-style URL query dialect (chumsky 0.13 + PyO3)
**Spec**: `sdd/specs/qsurl-parser.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-768, TASK-775
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 12, AC20. The primary consumers of qsurl are LLM agents and humans writing
URLs by hand; both need one reference page describing the grammar, the operator mapping, the
IR, per-provider capabilities, the error contract and the route. It is written last so it
documents the final route (TASK-775) and `to_gbnf()` (TASK-768), not the plan.

---

## Scope

- Write `docs/QSURL.md` with the sections listed in the blueprint, using examples that actually run (copy real outputs from `querysource.qsurl.parse` and a real 400 body from the handler tests).
- Add `tests/qsurl/test_docs.py` asserting the required headings exist and that every ```` ```qsurl ```` example in the page parses.

**NOT in scope**: README changes; docstrings in code (owned by each module's task).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `docs/QSURL.md` | CREATE | Dialect reference |
| `tests/qsurl/test_docs.py` | CREATE | Headings + runnable examples check |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.qsurl import parse, to_gbnf, QSUrlError     # TASK-765 / TASK-768
```

### Existing Signatures to Use
```text
docs/ holds flat markdown references, e.g. docs/PBAC_PROGRAMMATIC.md, docs/PER_TENANT_QUERIES.md, docs/QSSCHEDULER.md
Route (TASK-775): GET /api/v1/services/qsurl/{path:.*}   (+ ?q=<everything after the slug>)
Setting (TASK-773): QSURL_MAX_RESIDUAL_ROWS (navconfig, default 50000)
Capabilities (TASK-770): BaseProvider {select, filter, in_list, null_check}; sqlProvider + {alias, sort, limit, offset};
  pgProvider + {text_match}; cassandraProvider {select, filter, in_list, null_check, limit}, residual_scan=False
Operator mapping (spec §2 / proposal §3): = == → "=="; != ; < <= > >= ; ~ contains ; !~ not_contains ; ^= startswith ;
  $= endswith ; =~ regex ; =null / !col → is_null ; !=null / col → not_null ; lists only with = / !=
Error kinds: parse | lower | unsupported | cost ; 400 body = {"error","status","error_id","detail":{kind,offset,message,found,expected,pointer}}
```

### Does NOT Exist
- ~~`docs/QSURL.md`~~ — created here.
- ~~a docs build system (mkdocs/sphinx) wired for `docs/`~~ — plain markdown files.
- ~~`slug:format` on the qsurl route~~ — do not document it.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "docs/QSURL.md", "action": "CREATE"},
    {"path": "tests/qsurl/test_docs.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Draft the page with the headings below — *why*: `test_docs.py` pins them.
2. Generate every IR example with `parse()` and paste the real output — *why*: invented examples drift from the parser.
3. Take the 400 example from the handler test (`tests/handlers/test_qsurl_service.py`) — *why*: the envelope shape must match production.
4. Write `test_docs.py`.

### `docs/QSURL.md` (CREATE)
```markdown
# qsurl — URL query dialect

## Quick start
<!-- FILL IN: one path-form and one ?q= example against /api/v1/services/qsurl/, with curl and percent-encoding -->

## Grammar
<!-- FILL IN: the phase-1 grammar block (spec §6 "User-Provided Code"), whitespace rule, keywords -->

## Operators
<!-- FILL IN: URL → expression table incl. null sugar and list membership -->

## Pipeline operators
<!-- FILL IN: :sort(+a,-b) :top/:limit :skip/:offset :distinct; duplicates are errors -->

## Intermediate representation (IR)
<!-- FILL IN: real parse() output for the example query; `requires` vocabulary and its order -->

## Pushdown and residual evaluation
<!-- FILL IN: capability table per provider; what runs in memory; case-insensitive text ops; regex always residual -->

## Cost guard
<!-- FILL IN: QSURL_MAX_RESIDUAL_ROWS, residual_scan=False on Cassandra, kind "cost" -->

## Errors
<!-- FILL IN: kinds table; a real 400 body with detail and pointer -->

## Python API
<!-- FILL IN: parse, requires, HAS_RUST, QSUrlError, QSURL_FORCE_FALLBACK, to_gbnf() usage for constrained decoding -->

## Limits
<!-- FILL IN: ~8 KB URLs, %2F inside quoted strings, phase-2 features (functions, navigation) → 400 unsupported -->
```

### `tests/qsurl/test_docs.py` (CREATE)
```python
"""docs/QSURL.md keeps its structure and its examples parse (spec AC20)."""
from __future__ import annotations

import re
from pathlib import Path

from querysource.qsurl import parse

DOC = Path(__file__).resolve().parents[2] / "docs" / "QSURL.md"
HEADINGS = ("## Quick start", "## Grammar", "## Operators", "## Pipeline operators",
            "## Intermediate representation (IR)", "## Pushdown and residual evaluation",
            "## Cost guard", "## Errors", "## Python API", "## Limits")


def test_required_headings():
    text = DOC.read_text(encoding="utf-8")
    assert [h for h in HEADINGS if h not in text] == []


def test_qsurl_examples_parse():
    blocks = re.findall(r"```qsurl\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
    assert blocks, "document at least one ```qsurl example"
    # FILL IN: parse() every non-empty line of every block (lines marked `# error` are expected to raise QSUrlError)
```

### FILL IN checklist
- [ ] Every section body with real outputs.
- [ ] `test_qsurl_examples_parse` loop.

---

## Acceptance Criteria

- [ ] `docs/QSURL.md` covers grammar, operators, IR, capabilities per provider, error contract with a 400 example, `?q=` form, cost guard and `to_gbnf()` (AC20).
- [ ] Every ```` ```qsurl ```` example parses (or raises where marked).

---

## Validation Commands

- `pytest tests/qsurl/test_docs.py -q`

---

## Test Specification

See the `tests/qsurl/test_docs.py` block above.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at the path listed above for full context.
2. **Check dependencies** — verify every `Depends-on` task is `done` in `sdd/tasks/index/qsurl-parser.json`.
3. **Verify the Codebase Contract** before writing any code: re-run the `grep -c` for every MODIFY anchor; if a count changed, re-locate the anchor; if it is `0`, stop and report the drift.
4. **Update status** in `sdd/tasks/index/qsurl-parser.json` → `"in-progress"`.
5. **Implement** from the Implementation Blueprint; complete every `# FILL IN:`; never change a signature or path the blueprint fixes.
6. **Verify** every Acceptance Criterion and run the Validation Commands (`source .venv/bin/activate` first).
7. **Move this file** to `sdd/tasks/completed/TASK-778-qsurl-docs.md` and set the index entry to `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
