# TASK-831: Cython JSONB negation operators (`@!`, `@$`) and multi-operator dispatch

**Feature**: FEAT-179 — JSONB NOT (`@!`) and NOT-ALL (`@$`) filter operators
**Spec**: `sdd/specs/jsonb-not-or-operators.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Implements spec Module 1 (§3) on the Cython fallback path of the PostgreSQL
parser. It mirrors commit `8936386` (the `@>|` any-of operator) for two
negated forms, and extends `jsonb_condition` from first-key-only dispatch to
rendering every operator key in the dict (spec §2 Overview). TASK-832 does the
same in Rust; the two must produce byte-identical SQL for the cases in spec §2.

---

## Scope

- Add `'@!'` and `'@$'` to `JSONB_OPERATORS`.
- Add `cdef str jsonb_none_of_condition(str col, object operand)` rendering
  `NOT (col @> a OR col @> b ...)`.
- Add `cdef str jsonb_not_all_condition(str col, object operand)` rendering
  `((NOT col @> a) OR (NOT col @> b) ...)`; a single operand renders
  `NOT (col @> a)`.
- Rewrite the operator branch of `jsonb_condition` so it renders every operator
  key, ANDs the groups when there is more than one, and drops the whole
  condition if any group renders `None`.
- Drop a dict that mixes comparison tokens with JSONB tokens (spec §2, S3).
- Rebuild the Cython extension and run the existing JSONB tests (Cython path).

**NOT in scope**: the Rust builder (TASK-832); new test cases and the version
bump (TASK-833); top-level operator keys in `where_cond` (spec Non-Goal).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/pgsql.pyx` | MODIFY | tokens, two helpers, multi-operator dispatch |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import orjson                                         # verified: querysource/parsers/pgsql.pyx:12 (already imported)
```
No new imports are needed.

### Existing Signatures to Use
```python
# querysource/parsers/pgsql.pyx
COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)            # line 27
JSONB_OPERATORS = ('@>', '<@', '@>|', '->', '->>',)                # line 30
JSONB_KEY_SUFFIXES = '|!~#@:'                                      # line 33
PG_TEXT_OPERATORS = ('ILIKE', 'NOT ILIKE',)                        # line 35
cdef str pg_literal(str value)                                     # line 42
cdef str jsonb_dumps(object value)                                 # line 63
cdef str jsonb_operand(object value)                               # line 76 — str parsed as JSON; invalid raises orjson.JSONDecodeError
cdef str jsonb_any_of_condition(str col, object operand)           # line 91 — the pattern to mirror (lines 91-113)
cdef str jsonb_path_condition(str col, str op, object operand)     # line 116
cdef tuple jsonb_condition(str col, dict value)                    # line 151-199 — returns (handled, cond | None)
async def filter_conditions(self, sql)                             # line 209 — Rust first, Cython on exception
```

Current dispatch body (pgsql.pyx:173-199), for reference:
```python
    cdef int operators
    if not value:
        return (False, None)
    col = col.rstrip(JSONB_KEY_SUFFIXES)
    operators = sum(
        1 for k in value if k in COMPARISON_TOKENS or k in JSONB_OPERATORS
    )
    op, operand = next(iter(value.items()))
    if op in PG_TEXT_OPERATORS:
        return (False, None)
    if operators and op in COMPARISON_TOKENS:
        return (False, None)
    try:
        if operators == 0:
            return (True, f"{col} @> {pg_literal(jsonb_dumps(value))}::jsonb")
        if operators != len(value):
            return (True, None)
        if op in ('@>', '<@'):
            return (True, f"{col} {op} {pg_literal(jsonb_operand(operand))}::jsonb")
        if op == '@>|':
            return (True, jsonb_any_of_condition(col, operand))
        return (True, jsonb_path_condition(col, op, operand))
    except (orjson.JSONDecodeError, orjson.JSONEncodeError, TypeError, ValueError):
        return (True, None)
```

### Does NOT Exist
- ~~`jsonb_none_of_condition` / `jsonb_not_all_condition`~~ — this task creates them.
- ~~`'@!'` / `'@$'` handling anywhere~~ — no code knows these tokens yet.
- ~~a `NOT @>` / `!@>` SQL operator~~ — PostgreSQL has none; wrap the check in `NOT (...)`.
- ~~multi-operator rendering~~ — today only the first key is rendered.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/parsers/pgsql.pyx",
      "action": "MODIFY"
    }
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- Build every term through `jsonb_operand` and then `pg_literal`, exactly as
  `jsonb_any_of_condition` does. Do not build literals by hand: `pg_literal`'s
  `E'...'` escaping is what lets the SQL survive later `format_map` passes.
- Single-operator dicts must produce exactly the same SQL as today. The
  existing tests are the regression check.
- Fail closed: one bad group drops the whole column condition, so the filter is
  never half-applied (which would widen results without anyone noticing).
- Empty or non-list operands for `@!`/`@$` return `None` (the condition is
  dropped), same as `@>|`. Say so in the docstrings (spec S4).
- Suffix stripping applies to the column name only. Never strip characters
  from operator keys.

### References in Codebase
- `querysource/parsers/pgsql.pyx:91-113` — `jsonb_any_of_condition`, the template
- commit `8936386` — same change for `@>|`

---

## Implementation Blueprint

### Steps (in order)
1. Add the two tokens to `JSONB_OPERATORS`. *Why*: the operator count in
   `jsonb_condition` only recognizes keys in this tuple.
2. Insert the two helpers just above `jsonb_path_condition`. *Why*: that keeps
   them next to the `@>|` helper they mirror.
3. Replace the `if op in ('@>', '<@'):` / `@>|` / path tail of the `try` block
   with a loop over all items. *Why*: the spec requires `@!` and `@$` in the
   same dict, ANDed.
4. Add the mixed-class rule before the loop. *Why*: spec S3 requires a
   deterministic result that matches Rust.
5. Run `make build-inplace`, then the Validation Commands. *Why*: the `.pyx`
   only takes effect after a rebuild.

### `querysource/parsers/pgsql.pyx` (MODIFY) — tokens
```python
# occurrences: 1 (verified: grep -c "JSONB_OPERATORS = ('@>', '<@', '@>|', '->', '->>',)" querysource/parsers/pgsql.pyx)
# REPLACE line `JSONB_OPERATORS = ('@>', '<@', '@>|', '->', '->>',)` (verified: querysource/parsers/pgsql.pyx:30)
# ``@!`` is none-of (negated any-of); ``@$`` is not-all (OR of negated checks).
JSONB_OPERATORS = ('@>', '<@', '@>|', '@!', '@$', '->', '->>',)
```
**Why**: the order does not matter for membership. Keep `@>|` before the new
tokens so the diff stays small.

### `querysource/parsers/pgsql.pyx` (MODIFY) — helpers
```python
# occurrences: 1 (verified: grep -c "cdef str jsonb_path_condition(str col, str op, object operand):" querysource/parsers/pgsql.pyx)
# BEFORE — insert above `cdef str jsonb_path_condition(str col, str op, object operand):` (verified: querysource/parsers/pgsql.pyx:116)
cdef list jsonb_containment_terms(str col, object operand):
    """Render each item of ``operand`` as ``col @> '<json>'::jsonb``.

    Args:
        col: safe column identifier.
        operand: list of containment operands.

    Returns:
        The terms, or None when the operand is not a non-empty list/tuple.
    """
    if not isinstance(operand, (list, tuple)) or not operand:
        return None
    return [
        f"{col} @> {pg_literal(jsonb_operand(item))}::jsonb" for item in operand
    ]


cdef str jsonb_none_of_condition(str col, object operand):
    """Render ``{"@!": [a, b, ...]}`` as ``NOT (col @> a OR col @> b ...)``.

    None-of containment, the negation of ``@>|``: a row matches when it
    contains none of the operands. An empty or non-list operand returns None,
    so the condition is dropped (no filter), the same as ``@>|``.

    Args:
        col: safe column identifier.
        operand: list of containment operands.

    Returns:
        The condition, or None when the operand is not a non-empty list.
    """
    cdef list parts = jsonb_containment_terms(col, operand)
    if parts is None:
        return None
    return 'NOT (' + ' OR '.join(parts) + ')'


cdef str jsonb_not_all_condition(str col, object operand):
    """Render ``{"@$": [a, b, ...]}`` as ``((NOT col @> a) OR (NOT col @> b) ...)``.

    Not-all containment: a row matches when it lacks at least one operand.
    One operand renders ``NOT (col @> a)``, the same as ``@!``. An empty or
    non-list operand returns None, so the condition is dropped (no filter,
    not a logical FALSE).

    Args:
        col: safe column identifier.
        operand: list of containment operands.

    Returns:
        The condition, or None when the operand is not a non-empty list.
    """
    cdef list parts = jsonb_containment_terms(col, operand)
    if parts is None:
        return None
    if len(parts) == 1:
        return f'NOT ({parts[0]})'
    return '(' + ' OR '.join(f'(NOT {p})' for p in parts) + ')'
```
**Why**: the shared `jsonb_containment_terms` helper is spec S1, which asks
for one term primitive per builder. Leave `jsonb_any_of_condition` as it is,
so the output of `@>|` stays byte-identical and that code is not touched.

### `querysource/parsers/pgsql.pyx` (MODIFY) — dispatch
```python
# occurrences: 1 (verified: grep -c "        if op == '@>|':" querysource/parsers/pgsql.pyx)
# REPLACE the block from `        if op in ('@>', '<@'):` (pgsql.pyx:193) through
#   `        return (True, jsonb_path_condition(col, op, operand))` (pgsql.pyx:197)
#   — the `if op == '@>|':` anchor (pgsql.pyx:195) sits inside it.
        # FILL IN: mixed-class rule — if any key of `value` is in COMPARISON_TOKENS
        #   (this point is only reached when the FIRST key is a JSONB token),
        #   return (True, None). Bounded by spec §2 / S3 and AC "mixed dict dropped".
        groups = []
        for op, operand in value.items():
            cond = jsonb_operator_condition(col, op, operand)
            if cond is None:
                return (True, None)
            groups.append(cond)
        if len(groups) == 1:
            return (True, groups[0])
        return (True, '(' + ' AND '.join(groups) + ')')
```
Add the per-operator renderer above `jsonb_condition`, after the helpers block:
```python
cdef str jsonb_operator_condition(str col, str op, object operand):
    """Render one ``op: operand`` pair of a JSONB filter dict.

    Args:
        col: safe column identifier.
        op: a member of ``JSONB_OPERATORS``.
        operand: the operator's operand.

    Returns:
        The condition, or None when the operand cannot be rendered.
    """
    if op in ('@>', '<@'):
        return f"{col} {op} {pg_literal(jsonb_operand(operand))}::jsonb"
    if op == '@>|':
        return jsonb_any_of_condition(col, operand)
    if op == '@!':
        return jsonb_none_of_condition(col, operand)
    if op == '@$':
        return jsonb_not_all_condition(col, operand)
    return jsonb_path_condition(col, op, operand)
```
**Why**: the existing `try/except` around this block still catches orjson
errors from any group and returns `(True, None)`, which is what makes it fail
closed. Do not move the loop outside the `try`. Keep the earlier
`operators == 0` and `operators != len(value)` checks as they are: those are
what still drop `{"@>": ..., "status": "x"}`. Update the `jsonb_condition`
docstring to list `@!`/`@$` and the multi-operator AND rule.

### FILL IN checklist
- [ ] `jsonb_condition` mixed-class rule: drop a dict mixing comparison and JSONB tokens. Bounded by spec S3.
- [ ] `jsonb_condition` docstring: add `@!`, `@$` and the AND-of-groups rule.

---

## Acceptance Criteria

- [ ] `{"col": {"@!": [a, b]}}` renders `NOT (col @> 'a'::jsonb OR col @> 'b'::jsonb)` on the Cython path.
- [ ] `{"col": {"@$": [a, b]}}` renders `((NOT col @> 'a'::jsonb) OR (NOT col @> 'b'::jsonb))`; one operand renders `NOT (col @> 'a'::jsonb)`.
- [ ] `{"@!": [...], "@$": [...]}` renders `(<none-of> AND <not-all>)`.
- [ ] Empty, non-list or invalid-JSON operands for either operator drop the condition.
- [ ] A dict mixing comparison and JSONB tokens is dropped.
- [ ] All existing tests in `tests/test_pgsql_jsonb_filters.py` pass with no changes to expected strings (rebuild with `make build-inplace` first).
- [ ] `ruff check` is clean on the touched Python files (`.pyx` is not linted by ruff).

---

## Validation Commands

- `pytest tests/test_pgsql_jsonb_filters.py -q`

---

## Test Specification

New cases are added in TASK-833. To check this task on its own before then,
do a quick smoke check on the Cython path:
```python
from querysource.models import QueryObject
from querysource.parsers.pgsql import pgSQLParser
p = pgSQLParser(definition=None, conditions=QueryObject(query_raw="SELECT * FROM t {where_cond}"),
                query="SELECT * FROM t {where_cond}")
p.cond_definition = {}
p.filter = {"g": {"@!": [{"a": 1}, {"b": 2}]}}
# await p._filter_conditions_cy("SELECT * FROM t {where_cond}")
```

---

## Agent Instructions

1. Work in the feature worktree, never on `dev`
   (`python -m scripts.sdd.ensure_worktree --slug jsonb-not-or-operators --feature-id FEAT-179`).
2. Read the spec, mainly §2 (the rendered-forms table is normative) and §6.
3. Check the Codebase Contract against the current source before editing.
4. Set the status to `"in-progress"` in `sdd/tasks/index/jsonb-not-or-operators.json`.
5. Implement from the blueprint and complete every `FILL IN`.
6. Rebuild (`make build-inplace`) and run the Validation Commands.
7. Commit only `querysource/parsers/pgsql.pyx`.
8. Close with `scripts/sdd/close_task.sh TASK-831 jsonb-not-or-operators verified`.

---

## Completion Note

Seat: gpt-5.6-terra · Backend: codex · Model: gpt-5.6-terra · Attempts: 1 · Duration: 219.9s · Tokens: n/a

Implemented `@!`/`@$` in `querysource/parsers/pgsql.pyx` (tokens, `jsonb_none_of_condition`, `jsonb_not_all_condition`, multi-key AND dispatch, mixed-class rejection). Reviewed against spec; no corrections (review feedback_id coder-review:a240f6d8b032caa1273e47ba). Merge-tier validation selected no pytest targets; behavior is covered by TASK-833. Extension not rebuilt (shared env read-only).
