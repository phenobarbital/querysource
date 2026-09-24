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

**Completed by**: sdd-worker (Claude Sonnet 5, sequential fallback loop)
**Date**: 2026-09-24
**Notes**: Wrote `docs/QSURL.md` with all ten required sections (Quick start, Grammar,
Operators, Pipeline operators, Intermediate representation (IR), Pushdown and residual
evaluation, Cost guard, Errors, Python API, Limits) plus a short intro. Every example was
generated from a real run rather than invented: the IR block is the actual
`querysource.qsurl.parse()` output for the quick-start query; the pushdown REPL example
is the real `translate.split()` output against `pgProvider.capabilities`; the
`unsupported`/`cost` error JSON blocks are real `QSUrlError.to_dict()` output; the 400
envelope example is the real body `QSUrlService`/`AbstractHandler.Error` produces for
`stores?state='CA':order(name)` (built the same way `tests/handlers/test_qsurl_service.py`
does, `debug=False`) — `error_id` is random per run and left as one representative value,
noted implicitly by it not being asserted anywhere. Verified every JSON code block parses
with `json.loads` and every Python REPL snippet's claimed output matches a fresh run,
in addition to the `test_docs.py` "every ```qsurl block parses" check. Three ```qsurl```
blocks (Grammar's keyword-boundary examples, Operators' null/list/text-match examples,
Pipeline operators' combined example) — all valid queries, no `# error`-marked lines were
needed since no error-path yielding is documented as a runnable qsurl string (the error
JSON examples are documented as raw JSON, generated separately, not qsurl source).

Created `tests/qsurl/test_docs.py` per the blueprint: `test_required_headings` (all ten
present) and `test_qsurl_examples_parse` (the FILL IN loop — parses every non-empty line
of every ```qsurl block, treating a `# error`-suffixed line as an expected-`QSUrlError`
case; none of the current examples use that marker, but the mechanism is implemented and
tested-capable for future additions).

**Test results**: `pytest tests/qsurl/test_docs.py -q` -> 2 passed. Full regression:
`pytest tests/qsurl -q` -> 86 passed, 11 skipped (documented Rust-path skips, TASK-769).
`ruff check tests/qsurl/test_docs.py` clean.

**Deviations from spec**: none.

---

## Feature complete

All 15 tasks of FEAT-152 are now done. TASK-764's blocker (porting the reference Rust
crate — sandbox denial, "Code from External") was resolved by a privileged operator
between the two halves of this run; every task blocked on it (TASK-766/767/768/777/778)
was completed once it landed. Two real, previously-hidden bugs were found and fixed along
the way, both documented in their discovering task's own Completion Note: a double-quoting
bug in the TASK-769 PostgreSQL `ILIKE` builders (found via TASK-776's real-pipeline e2e
test) and a self-shadowing import bug in TASK-765's `querysource/qsurl/__init__.py`
(found via TASK-766's first real exercise of the public `parse()` API).
