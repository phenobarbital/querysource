# TASK-950: `UserFilterDenied` exception and typed `user_filter_options` models

**Feature**: FEAT-184 — PBAC-based per-user automatic filtering (`user_filtering`)
**Spec**: `sdd/specs/pbac-based-per-user-filter.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 1 (first half). Creates the 403-class denial exception and the
pure-Python validation layer for the `user_filter_options` jsonb document.
TASK-951 adds the compiler functions to the same module; TASK-954/955/956
raise or propagate `UserFilterDenied`.

---

## Scope

- Add `UserFilterDenied(QueryException)` to `querysource/exceptions.py`, before `class DriverError`.
- Create `querysource/auth/user_filter.py` with: module docstring, reason-code constants,
  `SUPERUSER_BYPASS`, `COLUMN_GRAMMAR`, the Pydantic models `UserFilterRule`,
  `ProgramRestriction`, `UserFilterOptions`, the dataclass `UserFilterResult`, and
  `parse_user_filter_options()`.
- Write `tests/auth/test_user_filter_options.py`.

**NOT in scope**: `session_userinfo`, `resolve_attribute`, `bypass_reason`, `check_program`,
`compile_user_filter` (TASK-951); any parser/provider/QS change.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/exceptions.py` | MODIFY | Add `UserFilterDenied` |
| `querysource/auth/user_filter.py` | CREATE | Constants, option models, result dataclass, parser of options |
| `tests/auth/test_user_filter_options.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.exceptions import QueryException, ConfigError   # verified: querysource/exceptions.py:6 / :33
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator  # pydantic v2, used at querysource/semantic/models.py:116 (ConfigDict) and querysource/queries/describe.py:54
```

### Existing Signatures to Use
```python
# querysource/exceptions.py
class QueryException(Exception):                       # line 6
    default_code: int = 500                            # line 13
    def __init__(self, message: str, code: int | None = None, **kwargs):   # line 16
        # sets self.message, self.args = kwargs (!), self.code = int(code) if code else self.default_code
class ConfigError(QueryException):                     # line 33
    def __init__(self, message: str = None): super().__init__(message or "QS Configuration Error.", code=500)
class QueryAccessDenied(QueryException):               # line 63 — code 404 BY DESIGN; do not reuse
class DriverError(QueryException):                     # line 74 — insert UserFilterDenied right before this

# querysource/semantic/models.py:116 — Pydantic style precedent
class GovernanceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
```

### Does NOT Exist
- ~~`querysource.auth.user_filter`~~ — this task creates it; do NOT add it to `querysource/auth/__init__.py`.
- ~~`UserFilterError`~~, ~~`UserFilterDenied`~~ — no 403-class exception exists yet.
- ~~`QueryException.reason_code`~~ — not a base-class attribute; define it on `UserFilterDenied` only.
- ~~passing `reason_code=` through `QueryException.__init__(**kwargs)`~~ — base `__init__` stores kwargs into `self.args`; set attributes explicitly instead.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/exceptions.py", "action": "MODIFY"},
    {"path": "querysource/auth/user_filter.py", "action": "CREATE"},
    {"path": "tests/auth/test_user_filter_options.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/exceptions.py#QueryException",
    "sym:querysource/exceptions.py#ConfigError"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- The module must import ONLY stdlib, `pydantic`, `querysource.auth.principal` (TASK-951) and
  `querysource.exceptions` — because the Cython parser imports it lazily and
  `querysource.auth.slug_visibility` would close an import cycle (spec §2).
- All models `ConfigDict(extra="forbid", frozen=True)`.
- `parse_user_filter_options()` converts every `ValidationError`/`ValueError`/`TypeError` into
  `ConfigError` — a bad document must never let the slug run unfiltered (AC-8).

---

## Implementation Blueprint

### Steps (in order)
1. Insert `UserFilterDenied` before `class DriverError` — *why*: keeps access exceptions grouped next to `QueryAccessDenied`.
2. Create `querysource/auth/user_filter.py` from the block below — *why*: fixes the public names every later task imports.
3. Write the tests — *why*: AC-3 and AC-8 are proven here.

### `querysource/exceptions.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '^class DriverError(QueryException):' querysource/exceptions.py)
# BEFORE — insert above `class DriverError(QueryException):` (verified: querysource/exceptions.py:74)
class UserFilterDenied(QueryException):
    """Per-user filtering refused to run the query (HTTP 403).

    The message is always generic and never names the rule, attribute or
    program that failed. ``reason_code`` carries the machine-readable cause
    for logs: ``no_principal``, ``authz_principal``, ``missing_attribute``,
    ``program_restricted`` or ``parser_required``.
    """

    default_code = 403

    def __init__(
        self,
        message: str | None = None,
        *,
        reason_code: str = "denied",
        slug: str | None = None,
    ) -> None:
        super().__init__(message or "Access denied.", code=403)
        self.reason_code = reason_code
        self.slug = slug


```
**Why**: `default_code = 403` plus explicit `code=403` so handlers answer 403 (AC-3); attributes are set after `super().__init__` because the base class overwrites `self.args`.

### `querysource/auth/user_filter.py` (CREATE)
```python
"""Per-user automatic filtering rules (``user_filtering`` / ``user_filter_options``).

Pure module: imports only ``querysource.auth.principal`` and
``querysource.exceptions`` so the Cython parser can import it lazily without
an import cycle and the multi-query follow-up can reuse it unchanged
(FEAT-184).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from ..exceptions import ConfigError

REASON_NO_PRINCIPAL: Final[str] = "no_principal"
REASON_AUTHZ_PRINCIPAL: Final[str] = "authz_principal"
REASON_MISSING_ATTRIBUTE: Final[str] = "missing_attribute"
REASON_PROGRAM_RESTRICTED: Final[str] = "program_restricted"
REASON_PARSER_REQUIRED: Final[str] = "parser_required"
SUPERUSER_BYPASS: Final[str] = "superuser"
COLUMN_GRAMMAR: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$"
)


class UserFilterRule(BaseModel):
    """One column constrained by one identity attribute."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    column: str
    attribute: str
    operator: Literal["eq", "in"] = "eq"
    required: bool = True

    @field_validator("column")
    @classmethod
    def _column_grammar(cls, value: str) -> str:
        """Reject anything but ``column`` or ``alias.column`` (it is spliced into SQL)."""
        # FILL IN: raise ValueError when COLUMN_GRAMMAR.fullmatch(value) is None — bounded by spec §2 column grammar
        return value

    @field_validator("attribute")
    @classmethod
    def _attribute_not_blank(cls, value: str) -> str:
        """Reject a blank attribute path."""
        # FILL IN: strip; raise ValueError when empty; return the stripped value
        return value


class ProgramRestriction(BaseModel):
    """Deny unless the definition's program_slug is among the caller's programs."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    restricted: bool = False
    allow_default: bool = True
    attribute: str = "programs"


class UserFilterOptions(BaseModel):
    """Validated ``user_filter_options`` document."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    rules: tuple[UserFilterRule, ...] = ()
    program: ProgramRestriction = ProgramRestriction()
    bypass: tuple[str, ...] = (SUPERUSER_BYPASS,)

    @model_validator(mode="after")
    def _declares_something(self) -> "UserFilterOptions":
        """A switch without rules nor program restriction is a configuration mistake."""
        # FILL IN: raise ValueError("user_filtering is on but user_filter_options declares nothing")
        #          when not self.rules and not self.program.restricted
        return self


@dataclass(frozen=True)
class UserFilterResult:
    """Outcome of compiling the rules for one caller (allow or bypass)."""

    predicates: dict[str, Any] = field(default_factory=dict)
    applied: tuple[str, ...] = ()
    bypassed: bool = False
    bypass_reason: str | None = None


def parse_user_filter_options(raw: Mapping[str, Any] | UserFilterOptions | None) -> UserFilterOptions:
    """Validate the jsonb document into ``UserFilterOptions``.

    Raises:
        ConfigError: ``raw`` is None/empty, not a mapping, fails validation, or
            declares neither rules nor a program restriction.
    """
    if isinstance(raw, UserFilterOptions):
        return raw
    # FILL IN: None / empty / non-Mapping → ConfigError("user_filter_options is required when user_filtering is on")
    # FILL IN: return UserFilterOptions.model_validate(dict(raw)); map ValidationError/ValueError/TypeError
    #          log the detail (logger.error, exc_info=True) and raise ConfigError with a CONSTANT
    #          message `from err` — never interpolate `{err}`: the pydantic report echoes admin-authored
    #          columns/attribute paths and failing input values straight into the HTTP response
    #          (TASK-960 / issue:4be97634adbe owns the final wording) — bounded by AC-8
    raise ConfigError("Invalid user_filter_options")
```
**Why this shape**: names, defaults and reason codes are fixed by spec §2 Data Models; TASK-951 appends functions below `parse_user_filter_options` and imports these constants, so do not rename them.

### `tests/auth/test_user_filter_options.py` (CREATE)
```python
"""FEAT-184 TASK-950 — UserFilterDenied and user_filter_options validation."""
from __future__ import annotations

import pytest

from querysource.auth.user_filter import UserFilterOptions, parse_user_filter_options
from querysource.exceptions import ConfigError, UserFilterDenied


def test_user_filter_denied_is_403_and_generic() -> None:
    err = UserFilterDenied(reason_code="missing_attribute", slug="s")
    assert err.code == 403
    assert str(err) == "Access denied."
    assert err.reason_code == "missing_attribute" and err.slug == "s"


def test_options_defaults() -> None:
    opts = parse_user_filter_options({"rules": [{"column": "user_id", "attribute": "user_id"}]})
    assert opts.bypass == ("superuser",)
    assert opts.rules[0].operator == "eq" and opts.rules[0].required is True
    assert opts.program.restricted is False and opts.program.allow_default is True


# FILL IN: test_options_rejects_unknown_keys — top-level and rule-level unknown key → ConfigError
# FILL IN: test_options_rejects_empty_document — None, {}, {"rules": []} → ConfigError
# FILL IN: test_options_program_only_is_valid — {"program": {"restricted": True}} parses
# FILL IN: test_options_rejects_bad_column_grammar — "user_id; DROP", "a.b.c", "" → ConfigError; "u.user_id" ok
# FILL IN: test_options_passthrough_instance — a UserFilterOptions instance is returned as-is (identity)
```

### FILL IN checklist
- [ ] `UserFilterRule._column_grammar` — fullmatch check; bounded by spec §2
- [ ] `UserFilterRule._attribute_not_blank` — strip + reject empty
- [ ] `UserFilterOptions._declares_something` — rules or program.restricted; bounded by AC-8
- [ ] `parse_user_filter_options` — None/non-mapping/validation → `ConfigError`; bounded by AC-8
- [ ] remaining test functions

---

## Acceptance Criteria

- [ ] `UserFilterDenied().code == 403`, message `"Access denied."`, `reason_code`/`slug` attributes (spec AC-3)
- [ ] Unknown keys, empty docs and bad column names raise `ConfigError` (spec AC-8)
- [ ] `python -c "import querysource.auth.user_filter"` succeeds and the module imports nothing from parsers/providers/queries/aiohttp/navigator_auth (spec AC-16)
- [ ] `ruff check querysource/auth/user_filter.py querysource/exceptions.py tests/auth/test_user_filter_options.py` clean

---

## Validation Commands

- `pytest tests/auth/test_user_filter_options.py -q`

---

## Test Specification

See the test block in the Implementation Blueprint.

---

## Agent Instructions

1. Work in the feature worktree (`parrot sdd worktree ensure --slug pbac-based-per-user-filter --feature-id FEAT-184`).
2. Read the spec; check `Depends-on` tasks are done in `sdd/tasks/index/pbac-based-per-user-filter.json`.
3. Verify the Codebase Contract before writing code.
4. Implement from the blueprint; complete every `# FILL IN:`; never change fixed names or paths.
5. Run the Validation Commands; commit only the listed files.
6. Close with `parrot sdd task close TASK-950 pbac-based-per-user-filter verified` and fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (FEAT-184)
**Date**:
**Notes**: Seat: gpt-5.6-sol (codex) · Attempts: 1 · merged by engine; lint commit e2e3fcb.

**Deviations from spec**: none
