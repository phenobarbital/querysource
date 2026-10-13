# TASK-873: Vocabulary promotes `FDOW`/`LDOW`/`LDOPM` to keywords

**Feature**: FEAT-167 — WHERE value hardening — `is_valid()` closure and idempotent literal quoting
**Spec**: `sdd/specs/where-value-hardening.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-872
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2 (vocabulary part) / §8 Q1 resolved. After TASK-872 the
default keyword list has 10 names, but `querysource/utils/vocabulary.py`
still lists `fdow`/`ldow` as informational `FunctionEntry` rows
(`invocable: False`, lines 131-149) and knows nothing about `ldopm`, so
`GET /api/v1/queries/vocabulary` (`handlers/describe.py:353`) would return the
three new keywords with `description: None`. This task adds the three
`KeywordEntry` rows, removes the two now-redundant `FunctionEntry` rows and
updates the test that pins the registry.

---

## Scope

- Add `"FDOW"`, `"LDOW"`, `"LDOPM"` `KeywordEntry` rows to `KEYWORD_REGISTRY`.
- Remove the `fdow` and `ldow` `FunctionEntry` rows from `FUNCTION_REGISTRY`
  (4 entries remain: `date_diff`, `date_sum`, `days_ago`, `previous_month`).
- Update `tests/unit/test_vocabulary.py::test_functions_informational_only`
  (size 6 → 4, order without `fdow`/`ldow`) and add
  `test_vocabulary_week_keywords`.

**NOT in scope**: `udf_list.py`/`to_udf` (TASK-872); the handler
(`describe.py` is unchanged — it already passes `udf_keywords()`); docs
(TASK-878).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/utils/vocabulary.py` | MODIFY | 3 keyword rows added (`:80-86`), 2 function rows removed (`:131-149`) |
| `tests/unit/test_vocabulary.py` | MODIFY | pinned registry size/order (`:47-52`), new test |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.types.validators import pg_constants, pg_udfs, udf_keywords   # verified: tests/unit/test_vocabulary.py:2
from querysource.utils.functions import fdom, fdow, ldow, ldopm                 # verified: functions.pyx:133, :147, :150, :206
from querysource.utils.vocabulary import FUNCTION_REGISTRY, KEYWORD_REGISTRY, KeywordEntry, build_vocabulary, keyword_example  # verified: vocabulary.py:88, :43, :18, :166, :154
```

### Existing Signatures to Use
```python
# querysource/utils/vocabulary.py
class KeywordEntry(BaseModel):          # line 18 — fields: name: str, category: Literal["date"] = "date", returns: str, description: str | None = None
class FunctionEntry(BaseModel):         # line 33 — fields: name, args: list[FunctionArg], description, invocable: bool = False
KEYWORD_REGISTRY: dict[str, KeywordEntry] = {   # line 43; last row:
    "CURRENT_MONTH": KeywordEntry(      # line 80
        name="CURRENT_MONTH",
        category="date",
        returns="integer",
        description="Current month number (1-12).",
    ),                                  # line 85
}                                       # line 86
FUNCTION_REGISTRY: tuple[FunctionEntry, ...] = (   # line 88
    FunctionEntry(name="date_diff", ...),          # line 89
    FunctionEntry(name="date_sum", ...),
    FunctionEntry(name="days_ago", ...),
    FunctionEntry(name="previous_month", ...),     # ends line 130 `    ),`
    FunctionEntry(                                 # line 131
        name="fdow",                               # line 132
        ...
        description="First day of the current week (Monday).",
        invocable=False,
    ),                                             # line 140
    FunctionEntry(                                 # line 141
        name="ldow",                               # line 142
        ...
        description="Last day of the current week (Sunday).",
        invocable=False,
    ),                                             # line 150
)                                                  # line 151
def keyword_example(name: str) -> Any              # line 154 — to_udf(name.strip()) in try/except → str | int | None
def build_vocabulary(udf_list, pg_constants, pg_udfs) -> dict  # line 166 — keywords follow udf_list order; registry miss → returns "unknown", description None

# tests/unit/test_vocabulary.py
def test_functions_informational_only():          # line 46
    assert len(FUNCTION_REGISTRY) == 6             # line 51
    assert function_names == ["date_diff", "date_sum", "days_ago", "previous_month", "fdow", "ldow"]  # line 52

# querysource/utils/functions.pyx — return formats (YYYY-MM-DD strings)
cpdef str fdow(...)   # line 147 — Monday of the current week
cpdef str ldow(...)   # line 150 — Sunday of the current week
cpdef str ldopm(...)  # line 206 — last day of the previous month
```

### Does NOT Exist
- ~~`KEYWORD_REGISTRY["FDOW"]`, `["LDOW"]`, `["LDOPM"]`~~ — not present today; added here.
- ~~`FunctionEntry(name="ldopm")`~~ — `ldopm` appears nowhere in `vocabulary.py`.
- ~~`KeywordEntry.example`~~ — the example is computed by `build_vocabulary` via `keyword_example`, not stored on the model.
- ~~`VOCABULARY_VERSION` bump~~ — stays `"1.0"`; not part of this task.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/utils/vocabulary.py", "action": "MODIFY"},
    {"path": "tests/unit/test_vocabulary.py", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/utils/vocabulary.py#KEYWORD_REGISTRY",
    "sym:querysource/utils/vocabulary.py#FUNCTION_REGISTRY",
    "sym:querysource/utils/vocabulary.py#KeywordEntry",
    "sym:querysource/utils/vocabulary.py#build_vocabulary",
    "sym:querysource/utils/vocabulary.py#keyword_example"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Pure Python; no rebuild needed — but `udf_keywords()` returns the 10-name
  list only after TASK-872's rebuilt extension is present in the worktree.
- `returns="date-string"` for all three (they return `YYYY-MM-DD` strings).

### References in Codebase
- `querysource/utils/vocabulary.py:56-68` — `FDOM`/`LDOM` rows: copy their shape.

---

## Implementation Blueprint

### Steps (in order)
1. Insert the three `KeywordEntry` rows after the `CURRENT_MONTH` row — *why*: `build_vocabulary` looks up by upper-cased name, so the keys must be upper-case.
2. Delete the `fdow` and `ldow` `FunctionEntry` rows (lines 131-150) — *why*: a name cannot be both a keyword and an "informational, not invocable" helper.
3. Update the pinned assertions in `test_functions_informational_only` and add `test_vocabulary_week_keywords` — *why*: AC2 pins 10 keywords / 4 functions.
4. `ruff check querysource/utils/vocabulary.py tests/unit/test_vocabulary.py`, then the Validation Commands.

### `querysource/utils/vocabulary.py` (MODIFY — keywords)
```python
# occurrences: 1 (verified: grep -c '    "CURRENT_MONTH": KeywordEntry(' querysource/utils/vocabulary.py)
# AFTER — insert below the closing `    ),` of the "CURRENT_MONTH" row (verified: vocabulary.py:85), before the dict's `}`
    "FDOW": KeywordEntry(
        name="FDOW",
        category="date",
        returns="date-string",
        description="First day (Monday) of the current week, YYYY-MM-DD.",
    ),
    "LDOW": KeywordEntry(
        name="LDOW",
        category="date",
        returns="date-string",
        description="Last day (Sunday) of the current week, YYYY-MM-DD.",
    ),
    "LDOPM": KeywordEntry(
        name="LDOPM",
        category="date",
        returns="date-string",
        description="Last day of the previous month, YYYY-MM-DD.",
    ),
```
**Why**: same shape as `FDOM`/`LDOM` (`vocabulary.py:56-68`); descriptions carry the format because `TODAY` differs (`MM/DD/YYYY`).

### `querysource/utils/vocabulary.py` (MODIFY — functions)
```python
# occurrences: 1 each (verified: grep -c '        name="fdow",' / '        name="ldow",' querysource/utils/vocabulary.py)
# DELETE the two `FunctionEntry(` blocks whose `name=` is "fdow" (lines 131-140) and "ldow" (lines 141-150).
# FUNCTION_REGISTRY ends with the `previous_month` entry's `    ),` followed by `)`.
```
**Why**: they are keywords now; leaving them would advertise `invocable=False` for names that ARE invocable.

### `tests/unit/test_vocabulary.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    assert len(FUNCTION_REGISTRY) == 6' tests/unit/test_vocabulary.py)
# REPLACE lines 51-52 with:
    assert len(FUNCTION_REGISTRY) == 4
    assert function_names == ["date_diff", "date_sum", "days_ago", "previous_month"]

# APPEND at end of file:
def test_vocabulary_week_keywords():
    """FEAT-167: FDOW/LDOW/LDOPM are keywords with descriptions and examples (AC2)."""
    from querysource.utils.functions import fdow, ldopm, ldow

    result = build_vocabulary(udf_keywords(), pg_constants(), pg_udfs())
    by_name = {k["name"]: k for k in result["keywords"]}
    assert [k["name"] for k in result["keywords"]] == udf_keywords()
    assert len(result["functions"]) == 4
    for name, fn in (("FDOW", fdow), ("LDOW", ldow), ("LDOPM", ldopm)):
        assert by_name[name]["returns"] == "date-string"
        assert by_name[name]["description"]
        # FILL IN: assert by_name[name]["example"] == fn() — bounded by AC2 (keyword_example resolves via to_udf)
```
**Why**: the docstring of `test_functions_informational_only` ("exactly 6 helpers") must be updated to "4" as well.

### FILL IN checklist
- [ ] `test_vocabulary.py::test_vocabulary_week_keywords` — example equality per keyword; bounded by AC2
- [ ] `test_vocabulary.py::test_functions_informational_only` docstring count updated to 4

---

## Acceptance Criteria

- [ ] `build_vocabulary(udf_keywords(), pg_constants(), pg_udfs())` lists 10 keywords with `FDOW`, `LDOW`, `LDOPM` described and exemplified, and 4 functions (spec AC2 part).
- [ ] `ruff check querysource/utils/vocabulary.py tests/unit/test_vocabulary.py` clean.

---

## Validation Commands

- `pytest tests/unit/test_vocabulary.py -q`

---

## Test Specification

See the `tests/unit/test_vocabulary.py` blueprint block above.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug where-value-hardening --feature-id FEAT-167`)
2. **Read the spec** at the path listed above for full context
3. **Check dependencies** — TASK-872 must be `"done"` in `sdd/tasks/index/where-value-hardening.json`
4. **Verify the Codebase Contract** — before writing ANY code
5. **Update status** in the per-spec index → `"in-progress"` and commit only that index file
6. **Implement** from the Implementation Blueprint
7. **Verify** — run the Validation Commands
8. **Commit the code** — stage only the files this task lists
9. **Close the task** with `scripts/sdd/close_task.sh TASK-873 where-value-hardening verified`
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

Implemented by seat gpt-5.6-luna (codex), 1 attempt, 41s, merged.
- `utils/vocabulary.py`: FDOW/LDOW/LDOPM keyword rows added; fdow/ldow function rows removed.
- `tests/unit/test_vocabulary.py`: registry pins updated + `test_vocabulary_week_keywords`.
- Validation: merge tier 5 passed.
- Review: orchestrator scope review, no fixes.
- Closed via close_task.sh (no durable review EvidenceRef for finalize_task).
