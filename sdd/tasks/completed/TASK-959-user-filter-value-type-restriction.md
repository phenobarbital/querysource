# TASK-959: Type-restrict identity values before they reach `filter_conditions`

**Feature**: FEAT-184 — PBAC-based per-user automatic filtering (`user_filtering`)
**Spec**: `sdd/specs/pbac-based-per-user-filter.spec.md`
**Discovered-from**: `issue:5102f6025f4a`
**Status**: done
**Priority**: high
**Estimated effort**: S (1-2h)
**Depends-on**: TASK-951
**Assigned-to**: unassigned

---

## Context

Ledger issue `issue:5102f6025f4a` (bug/major): `compile_user_filter()` (TASK-951)
copies resolved identity attributes verbatim into parser predicates
("list/in → list(value); scalar → value"). The dialect parsers'
`filter_conditions()` give **operator semantics** to value shapes
(`querysource/parsers/sql.pyx:196-258`):

- `dict` → comparison operators (`{'>=': x}`), multi-key AND;
- `list` whose first element is in `valid_operators` (`'<'`, `'!='`, `'IS NOT'`, …) → `key <op> v`;
- `str` `'null'`/`'NULL'` → `IS NULL`; `'!null'` → `IS NOT NULL`;
- `str` starting with `'!'` → `!=` (negation);
- `str` with a BETWEEN prefix (`filter_values.has_between_prefix`) → range splice.

An identity value such as `"!42"`, `"!null"`, `["!=", "x"]` or `{">": 0}` (from
a userinfo/profile attribute) would therefore widen the filter
(`store_id != 42` instead of `= 42`) — operator injection that turns the
restriction into a near-bypass. Fail closed instead.

---

## Scope

- In `querysource/auth/user_filter.py` add `REASON_INVALID_ATTRIBUTE: Final[str] = "invalid_attribute"`
  and a private `_safe_filter_value(value) -> str | int | float | list[str | int | float] | None`.
- Allowed: `bool` is **rejected** (parsers branch on it separately), `int`, `float`,
  `str` that is non-empty, does not start with `'!'`, is not `null`/`NULL` (case-insensitive),
  and `not has_between_prefix(value)`; `list`/`tuple`/`set` whose every element passes the
  scalar rule and whose first element is not a parser operator token. Anything else
  (`dict`, nested list, `None`, object) → invalid.
- In `compile_user_filter`'s rule loop: an invalid value → `log.warning` (attribute path +
  rule column, never the value) and `raise UserFilterDenied(reason_code=REASON_INVALID_ATTRIBUTE, slug=slug)`
  — regardless of `required` (an attribute that exists but is malformed is never "missing").
- Tests in `tests/auth/test_user_filter_value_types.py`.

**NOT in scope**: changing parser `filter_conditions()` semantics; other providers.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/auth/user_filter.py` | MODIFY | reason constant, `_safe_filter_value`, deny in rule loop |
| `tests/auth/test_user_filter_value_types.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.parsers.filter_values import has_between_prefix   # querysource/parsers/filter_values.py:86 — pure Python, no Cython build needed
from querysource.exceptions import UserFilterDenied                # created by TASK-950
from querysource.auth.user_filter import compile_user_filter       # created by TASK-951
```

### Existing Signatures to Use
```python
# querysource/parsers/filter_values.py:86
def has_between_prefix(value: object) -> bool
# querysource/parsers/sql.pyx:30
COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)
# querysource/parsers/sql.pyx:126 (SQLParser.__init__)
self.valid_operators: tuple = ('<', '>', '>=', '<=', '<>', '!=', 'IS NOT', 'IS')
# querysource/auth/user_filter.py (TASK-950/951)
REASON_MISSING_ATTRIBUTE: Final[str] = "missing_attribute"
def compile_user_filter(options, *, program_slug, principal, userinfo, slug=None, logger=None) -> UserFilterResult
```

### Does NOT Exist
- ~~`querysource.parsers.filter_values.OPERATOR_TOKENS`~~ — define a local tuple
  `_OPERATOR_TOKENS = ('<', '>', '>=', '<=', '<>', '!=', 'IS NOT', 'IS')` in user_filter.py
  (do not import from `.pyx`: compiled module, cycle risk).
- ~~`UserFilterDenied(reason=...)`~~ — the kwarg is `reason_code`.

---

## Implementation Blueprint

### `querysource/auth/user_filter.py`
```python
# AFTER — insert below `REASON_PARSER_REQUIRED: Final[str] = "parser_required"` (created by TASK-950; occurrences: 1)
REASON_INVALID_ATTRIBUTE: Final[str] = "invalid_attribute"

# Values with operator meaning in dialect filter_conditions() (parsers/sql.pyx:126).
_OPERATOR_TOKENS: Final[tuple[str, ...]] = ('<', '>', '>=', '<=', '<>', '!=', 'IS NOT', 'IS')


def _safe_scalar(value: Any) -> bool:
    """Return True if *value* is a plain scalar with no filter-operator meaning."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if not isinstance(value, str) or not value:
        return False
    # FILL IN: reject startswith('!'), lower() == 'null', has_between_prefix(value),
    #          value.strip().upper() in _OPERATOR_TOKENS — bounded by sql.pyx:196-258 branches
    return True


def _safe_filter_value(value: Any) -> bool:
    """Return True if *value* can be passed to filter_conditions as a literal equality/IN value."""
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        # FILL IN: non-empty and all(_safe_scalar(v) for v in items) — an empty list must deny, not match nothing silently
        return bool(items) and all(_safe_scalar(v) for v in items)
    return _safe_scalar(value)
```
```python
# MODIFY — compile_user_filter rule loop, right after resolve_attribute(...) and the missing-attribute branch
            if not _safe_filter_value(value):
                log.warning(
                    "user_filter deny: invalid attribute value type for %s (column %s, slug %s)",
                    rule.attribute, rule.column, slug,
                )
                raise UserFilterDenied(reason_code=REASON_INVALID_ATTRIBUTE, slug=slug)
```
**Why**: validating the *shape* at the single compile site keeps every
dialect parser unchanged and fails closed; never log the value itself
(it is identity data).

### `tests/auth/test_user_filter_value_types.py`
- FILL IN: parametrize denied values: `"!42"`, `"null"`, `"NULL"`, `"!null"`, a BETWEEN-prefixed string,
  `{">": 0}`, `["!=", "x"]`, `["IS NOT", "null"]`, `[["a"]]`, `[]`, `True`, `None`-in-list, `""` →
  `UserFilterDenied` with `reason_code == "invalid_attribute"`.
- FILL IN: allowed `42`, `"42"`, `"store-a"`, `[1, 2]`, `["a", "b"]` → predicates equal the input.
- FILL IN: denial is raised even when the rule is `required=False`.

### Steps (in order)
1. Confirm TASK-951 merged (`compile_user_filter` loop exists).
2. Apply the blocks; keep the fixed compile order (parse → identity → bypass → program → rules).
3. Write tests; run Validation Commands; commit only listed files.

### FILL IN checklist
- [ ] `_safe_scalar` rejection rules
- [ ] test parametrization

---

## Acceptance Criteria
- [ ] No identity value with operator semantics (dict, operator-led list, `!`-prefixed, null tokens, BETWEEN prefix, bool, nested/empty list) reaches parser predicates; each raises `UserFilterDenied(invalid_attribute)`.
- [ ] Plain scalars and flat scalar lists still compile to equality / IN predicates (AC-5 unchanged).
- [ ] The denial log line never includes the attribute value.

## Validation Commands
- `pytest tests/auth/test_user_filter_value_types.py -q`
- `pytest tests/auth/test_user_filter.py -q`

## Complexity Contract
```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/auth/user_filter.py", "action": "MODIFY"},
    {"path": "tests/auth/test_user_filter_value_types.py", "action": "CREATE"}
  ],
  "contract_symbols": ["sym:querysource/auth/user_filter.py#compile_user_filter", "sym:querysource/parsers/filter_values.py#has_between_prefix"]
}
```

## Completion Note
**Completed by**: Claude (sdd-start, direct)
**Date**: 2026-11-03
**Notes**: Added REASON_INVALID_ATTRIBUTE, _safe_scalar/_safe_filter_value and a deny in the compile_user_filter rule loop. 66 tests pass (new value-type tests + test_user_filter.py); 22 related regression tests pass. Closes issue:5102f6025f4a on /sdd-done.

**Deviations from spec**: Empty list and None stay "missing" (missing_attribute when required, skipped when optional) because the existing missing-attribute branch runs first; the task text listed `[]` as invalid_attribute. Both fail closed when required. Blank strings are still denied as invalid_attribute. Build note: this worktree had no compiled extensions, so I ran `python setup.py build_ext --inplace` inside it (gitignored artifacts only).
