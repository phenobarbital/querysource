---
# SDD flow type and base branch (FEAT-145).
type: feature
base_branch: dev
projects: [parsers]
tags: [parsers, filter-values, between, placeholders, tech-debt, sdd-fix]
---

# Feature Specification: filter_values / abstract edge cases — BETWEEN key negation and template placeholder detection

**Feature ID**: FEAT-170
**Date**: 2026-10-13
**Author**: Jesus Lara (spec drafted with Claude Code via /sdd-fix)
**Status**: approved
**Target version**: next patch
**Ledger issues**: `issue:1149c6ed91ea` (tech_debt, minor, discovered from spec:FEAT-165)

---

## 1. Motivation & Business Requirements

### Problem Statement

Two edge cases left by FEAT-165:

1. **`key_negated` depends on suffix order.** `parse_between()`
   (`querysource/parsers/filter_values.py:109`) treats a key as negated only
   when `"!"` is the *last* suffix character:
   `base_key(key) != key and key.rstrip().endswith("!")`. A key such as
   `amount!~` is therefore not negated. Because `set_conditions` collapses a
   BETWEEN key to `base_key(key)` (`abstract.pyx:596,621`), every other suffix
   is discarded, so the negation the caller wrote is silently lost and the
   opposite rows are returned.
2. **Placeholder detection is a substring test.** `set_conditions`
   (`querysource/parsers/abstract.pyx:561`) decides whether an explicitly
   filtered typed column is a template placeholder with
   `'{' + base + '}' not in template`. That misses placeholders that carry a
   conversion or format spec (`{since!s}`, `{since:>10}`), and it matches an
   escaped literal `{{since}}`, which `format_map` renders as the text
   `{since}`, not a placeholder. In the escaped case the typed filter is
   routed to placeholder substitution and never reaches the WHERE clause.

### Goals

- G1: any `"!"` in a key's suffix (the characters `base_key()` strips) negates
  a BETWEEN clause, whatever the suffix order.
- G2: placeholder detection uses `string.Formatter().parse()` field names
  (conversion, format spec, attribute and index parts stripped; escaped `{{ }}`
  ignored). Fall back to the current substring test when the template is not
  a valid format string (`ValueError`).

### Non-Goals (explicitly out of scope)

- The builders' own last-character suffix handling (`sql.pyx`, `pgsql.pyx`
  `end == '!'`) for non-BETWEEN values.
- Rust mirrors: `parse_between` and `set_conditions` have no Rust
  counterpart.
- Changing the OR semantics of `NOT BETWEEN` combined with a `!` key
  (`test_parse_between_negated` stays green).

## 2. Architectural Design

Add one pure-Python helper `template_fields(template: str) -> frozenset[str]`
to `filter_values.py`. `AbstractParser.set_conditions` computes it once per
call, before the loop, and tests `base not in fields`. `parse_between`
derives negation from the stripped suffix.

### New Public Interfaces

```python
# querysource/parsers/filter_values.py
def template_fields(template: str) -> frozenset[str]: ...
```

## 3. Module Breakdown

### Module 1: filter_values + abstract fixes

- `querysource/parsers/filter_values.py`: fix `key_negated`, add
  `template_fields`, export it in `__all__`.
- `querysource/parsers/abstract.pyx`: import `template_fields` and use it in
  `set_conditions` (Cython: needs a rebuild before tests see it).
- `tests/test_filter_values.py`: unit tests for both helpers.
- `tests/test_dialect_typed_filters.py`: parser-level tests for the escaped
  and conversion placeholder forms.

## 4. Test Specification

- `parse_between("amount!~", "BETWEEN 1 AND 2").negated is True`;
  `parse_between("amount~!", ...)` stays True; `parse_between("amount~", ...)`
  stays False.
- `template_fields("a {x} {y!s} {z:>3} {{w}} {o.attr} {i[0]}") == {"x","y","z","o","i"}`.
- `template_fields("{")` falls back without raising.
- Typed filter with template `... {{since}} {and_cond}` → the since filter is
  rendered in the WHERE/AND clause.
- Typed filter with template `... >= {since!s} {and_cond}` → placeholder
  substitution is kept (no duplicate WHERE filter).

## 5. Acceptance Criteria

- [ ] AC1: a `!` anywhere in the key suffix negates BETWEEN; existing negation tests pass.
- [ ] AC2: `template_fields` returns base field names and ignores escaped braces.
- [ ] AC3: `set_conditions` uses `template_fields`; the escaped-brace and conversion cases behave per §4.
- [ ] AC4: `pytest tests/test_filter_values.py tests/test_dialect_typed_filters.py` passes after a Cython rebuild.
- [ ] AC5: `ruff check querysource/parsers/filter_values.py tests/test_filter_values.py tests/test_dialect_typed_filters.py` is clean.

## 6. Codebase Contract

### Verified Imports
```python
from .filter_values import TYPED_FILTER_FORMATS, base_key, is_comparison_dict, parse_between  # abstract.pyx:21
from querysource.parsers.filter_values import (...)  # tests/test_filter_values.py:7
```

### Existing Signatures
```python
# querysource/parsers/filter_values.py
KEY_SUFFIX_CHARS: str = "|!~#@:"                      # :15
def base_key(key: str) -> str:                         # :44  key.rstrip(KEY_SUFFIX_CHARS)
def parse_between(key: str, value: str) -> BetweenClause | None:  # :95
# querysource/parsers/abstract.pyx
async def set_conditions(self, conditions: dict, connection: object) -> dict:  # :548
```

### Edit Sites
| File | Anchor | Line | Occurrences |
|---|---|---|---|
| `querysource/parsers/filter_values.py` | `key_negated = base_key(key) != key and key.rstrip().endswith("!")` | 109 | 1 |
| `querysource/parsers/abstract.pyx` | `template = self.query_raw or ''` | 555 | 1 |
| `querysource/parsers/abstract.pyx` | `and '{' + base + '}' not in template` | 561 | 1 |

### Does NOT Exist (Anti-Hallucination)
- ~~`filter_values.template_fields`~~ — created by this feature.
- ~~a Rust `parse_between`~~ — not mirrored in `rust/src/`.

## 7. Implementation Notes & Constraints

- `abstract.pyx` is Cython: run `python setup.py build_ext --inplace` before
  Python-level tests.
- Keep `parse_between`'s OR semantics between `NOT` and `!`.
