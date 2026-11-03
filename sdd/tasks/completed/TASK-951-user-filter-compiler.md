# TASK-951: Rule compiler — attribute resolution, bypass, program check, `compile_user_filter`

**Feature**: FEAT-184 — PBAC-based per-user automatic filtering (`user_filtering`)
**Spec**: `sdd/specs/pbac-based-per-user-filter.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-950
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 1 (second half). Adds the functions that turn validated options
plus the caller's identity into parser predicates, or raise `UserFilterDenied`.
This is the single deny site of the feature: callers never decide to raise.

---

## Scope

- Append to `querysource/auth/user_filter.py`: `session_userinfo()`, `resolve_attribute()`,
  `bypass_reason()`, `check_program()`, `compile_user_filter()` and a private `_normalize_programs()`.
- Write `tests/auth/test_user_filter.py`.

**NOT in scope**: parser, provider and QS wiring; changing the models from TASK-950.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/auth/user_filter.py` | MODIFY | Append compiler functions |
| `tests/auth/test_user_filter.py` | CREATE | Unit tests for the compiler |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.auth.principal import QSPrincipal   # verified: querysource/auth/principal.py:21
from querysource.exceptions import UserFilterDenied   # created by TASK-950 (querysource/exceptions.py, before DriverError)
# Inside user_filter.py use relative imports: from .principal import QSPrincipal ; from ..exceptions import ConfigError, UserFilterDenied
# Optional, guarded exactly like querysource/semantic/governance.py:22-25:
#   try: from navigator_auth.conf import AUTH_SESSION_OBJECT
#   except ImportError: AUTH_SESSION_OBJECT = "user"
```

### Existing Signatures to Use
```python
# querysource/auth/principal.py — frozen dataclass
class QSPrincipal:                                   # line 21
    user_id: str; username: str | None; groups: tuple[str, ...]; roles: tuple[str, ...]
    programs: tuple[str, ...]; superuser: bool; tenant_id: str | None; channel: str; authz_backend: str | None   # lines 32-40
    @property
    def is_authz(self) -> bool                       # line 93
    def log_fields(self) -> dict[str, Any]           # line 120
    @classmethod for_authz(...)                      # authz form: user_id "authz:<backend>", groups ("authorized", backend)

# querysource/semantic/governance.py:17-45 — session → principal pattern to mirror
userinfo = session.get(AUTH_SESSION_OBJECT, session.get("user", {}))   # line 27
if not isinstance(userinfo, dict): return None                        # line 28

# querysource/auth/slug_visibility.py:67 normalize_programs — DO NOT import (cycle); reimplement:
#   str → lower().strip(); dict → .get('slug') or .get('name'); object with .slug / .name; skip None/empty
```

### Does NOT Exist
- ~~importing `querysource.auth.slug_visibility` or `querysource.semantic.governance` from user_filter.py~~ — forbidden (import cycle, spec §2).
- ~~`QSPrincipal.attributes`~~ / ~~`QSPrincipal.get()`~~ — no generic attribute bag; extra attributes come only from `userinfo`.
- ~~`QSPrincipal.from_session`~~ — conversion lives in `principal_from_session` (governance.py:17).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/auth/user_filter.py", "action": "MODIFY"},
    {"path": "tests/auth/test_user_filter.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/auth/principal.py#QSPrincipal",
    "sym:querysource/semantic/governance.py#principal_from_session"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Order inside `compile_user_filter` is fixed (spec §3): parse → no identity → bypass → program → rules.
- Missing = `None` OR empty list/tuple. Required + missing → deny; optional + missing → skip (AC-5).
- List value or `operator="in"` → list predicate (a scalar with `in` becomes `[scalar]`); scalar → scalar.
  Tuples become lists. Never render an empty list.
- Authz principal: claims other than `groups`/`roles` count as absent → `reason_code="authz_principal"`
  for a required rule whose first segment is a principal claim and resolves absent.
- Logging: deny → `logger.warning("user_filter denied slug=%s reason=%s %s", slug, code, principal.log_fields() if principal else {})`
  before raising; bypass → `logger.info(...)` with the reason. Use `logging.getLogger("QS.UserFilter")` when no logger is passed.
- Never mutate `options`, `userinfo` or the principal.

---

## Implementation Blueprint

### Steps (in order)
1. Add the new imports at the top of `user_filter.py` — *why*: only `principal` and `exceptions` are allowed (AC-16).
2. Append the functions below after `parse_user_filter_options` — *why*: TASK-954 and TASK-956 import exactly these names.
3. Write the tests.

### `querysource/auth/user_filter.py` (MODIFY — imports)
```python
# occurrences: 1 (verified after TASK-950: grep -c '^from ..exceptions import ConfigError$' querysource/auth/user_filter.py)
# REPLACE `from ..exceptions import ConfigError` with:
import logging

from ..exceptions import ConfigError, UserFilterDenied
from .principal import QSPrincipal

try:
    from navigator_auth.conf import AUTH_SESSION_OBJECT
except ImportError:  # navigator-auth is optional (pattern: semantic/governance.py:22-25)
    AUTH_SESSION_OBJECT = "user"

_logger = logging.getLogger("QS.UserFilter")
_PRINCIPAL_CLAIMS: Final[frozenset[str]] = frozenset(
    {"user_id", "username", "groups", "roles", "programs", "superuser", "tenant_id"}
)
_AUTHZ_CLAIMS: Final[frozenset[str]] = frozenset({"groups", "roles"})
```

### `querysource/auth/user_filter.py` (MODIFY — append at end of file)
```python
def session_userinfo(session: Any | None) -> dict[str, Any]:
    """Return the raw navigator-auth userinfo dict of a session, or ``{}``."""
    if session is None or not hasattr(session, "get"):
        return {}
    userinfo = session.get(AUTH_SESSION_OBJECT, session.get("user", {}))
    return dict(userinfo) if isinstance(userinfo, dict) else {}


def resolve_attribute(path: str, principal: QSPrincipal | None, userinfo: Mapping[str, Any] | None) -> Any:
    """Resolve a dotted path: typed principal claim first, then ``userinfo``; ``None`` when absent."""
    head, *rest = path.split(".")
    value: Any = None
    # FILL IN: if principal and head in _PRINCIPAL_CLAIMS and (not principal.is_authz or head in _AUTHZ_CLAIMS):
    #          value = getattr(principal, head)
    # FILL IN: if value is None (or an empty tuple from a claim) and userinfo: value = userinfo.get(head)
    # FILL IN: walk `rest` through Mappings; any non-mapping/missing segment → return None
    # FILL IN: tuple → list
    return value


def bypass_reason(options: UserFilterOptions, principal: QSPrincipal | None) -> str | None:
    """``"superuser"`` / ``"group:<name>"`` / ``"role:<name>"`` when the principal matches ``options.bypass``."""
    if principal is None:
        return None
    # FILL IN: SUPERUSER_BYPASS in options.bypass and principal.superuser → "superuser"
    # FILL IN: case-insensitive match of other bypass names against principal.groups then principal.roles
    return None


def _normalize_programs(raw: Any) -> set[str]:
    """str / dict(slug|name) / objects with .slug|.name → lower-case slugs."""
    # FILL IN: mirror querysource/auth/slug_visibility.py:67-95 without importing it
    return set()


def check_program(
    options: UserFilterOptions,
    program_slug: str | None,
    principal: QSPrincipal | None,
    userinfo: Mapping[str, Any] | None,
    *,
    slug: str | None = None,
) -> None:
    """Raise ``UserFilterDenied(program_restricted)`` unless the program check passes."""
    program = options.program
    if not program.restricted:
        return
    # FILL IN: slug_lc = (program_slug or "").strip().lower(); allow_default and slug_lc == "default" → return
    # FILL IN: programs = _normalize_programs(resolve_attribute(program.attribute, principal, userinfo))
    # FILL IN: not slug_lc or slug_lc not in programs → raise UserFilterDenied(reason_code=REASON_PROGRAM_RESTRICTED, slug=slug)


def compile_user_filter(
    options: Mapping[str, Any] | UserFilterOptions | None,
    *,
    program_slug: str | None,
    principal: QSPrincipal | None,
    userinfo: Mapping[str, Any] | None,
    slug: str | None = None,
    logger: logging.Logger | None = None,
) -> UserFilterResult:
    """Compile the definition's rules into parser predicates for one caller (see spec §3 Module 1)."""
    log = logger or _logger
    opts = parse_user_filter_options(options)
    # FILL IN: principal is None and not userinfo → warn + raise UserFilterDenied(REASON_NO_PRINCIPAL) — AC-4
    # FILL IN: reason = bypass_reason(opts, principal); if reason → log.info(audit) and
    #          return UserFilterResult(bypassed=True, bypass_reason=reason) — AC-7
    # FILL IN: check_program(...) wrapped to log WARNING before re-raising — AC-6/AC-7
    # FILL IN: loop opts.rules: resolve; missing → required ? deny (authz vs missing_attribute code) : continue;
    #          list/in → list(value) ; scalar → value ; collect predicates + applied — AC-5
    return UserFilterResult()
```
**Why this shape**: the compiler raises itself so there is a single fail-closed deny site; `UserFilterResult` only exists for allow/bypass.

### `tests/auth/test_user_filter.py` (CREATE)
```python
"""FEAT-184 TASK-951 — compile_user_filter and helpers."""
from __future__ import annotations

import pytest

from querysource.auth.principal import QSPrincipal
from querysource.auth.user_filter import compile_user_filter, resolve_attribute, session_userinfo
from querysource.exceptions import UserFilterDenied

OPTIONS = {
    "rules": [
        {"column": "user_id", "attribute": "user_id"},
        {"column": "store_id", "attribute": "attributes.store_id", "operator": "in"},
    ],
    "program": {"restricted": True, "allow_default": True},
    "bypass": ["superuser", "admins"],
}


@pytest.fixture
def principal() -> QSPrincipal:
    return QSPrincipal(user_id="7", username="ana", groups=("sales",), programs=("epson",), channel="http")


@pytest.fixture
def userinfo() -> dict:
    return {"user_id": "999", "programs": ["epson"], "attributes": {"store_id": [11, 12]}}


def test_compile_scalar_and_list_predicates(principal, userinfo) -> None:
    result = compile_user_filter(OPTIONS, program_slug="epson", principal=principal, userinfo=userinfo)
    assert result.predicates == {"user_id": "7", "store_id": [11, 12]}
    assert result.applied == ("user_id", "store_id")


def test_compile_no_principal_denies() -> None:
    with pytest.raises(UserFilterDenied) as exc:
        compile_user_filter(OPTIONS, program_slug="epson", principal=None, userinfo={})
    assert exc.value.reason_code == "no_principal" and exc.value.code == 403


# FILL IN: test_resolve_attribute_principal_first_then_userinfo (user_id "7" wins over "999")
# FILL IN: test_resolve_attribute_authz_principal_claims_absent (QSPrincipal.for_authz → user_id None)
# FILL IN: test_compile_missing_required_attribute_denies (absent / None / [] ; required: false skipped)
# FILL IN: test_compile_superuser_bypass (bypassed True, no predicates, program not checked)
# FILL IN: test_compile_group_role_bypass_case_insensitive ("Admins" vs group "admins"; role → "role:<n>")
# FILL IN: test_check_program_default_allowed / allow_default False denies
# FILL IN: test_check_program_restricted_denies (programs as str, dicts, objects with .slug)
# FILL IN: test_session_userinfo_fallback_key (missing key → "user"; non-dict → {}; None → {})
# FILL IN: test_compile_does_not_mutate_inputs
```

### FILL IN checklist
- [ ] `resolve_attribute` — claim-then-userinfo, authz rule, dotted walk; bounded by spec §3
- [ ] `bypass_reason` — superuser literal, case-insensitive groups/roles; AC-7
- [ ] `_normalize_programs` — mirror slug_visibility without importing it
- [ ] `check_program` — default handling, deny; AC-6
- [ ] `compile_user_filter` — fixed order, logging, predicates; AC-4/5/7
- [ ] remaining tests

---

## Acceptance Criteria

- [ ] Spec AC-4, AC-5, AC-6, AC-7 proven by tests
- [ ] Module still imports only `pydantic`, stdlib, `.principal`, `..exceptions` and guarded `navigator_auth.conf` (AC-16)
- [ ] `ruff check querysource/auth/user_filter.py tests/auth/test_user_filter.py` clean

---

## Validation Commands

- `pytest tests/auth/test_user_filter.py -q`

---

## Test Specification

See the test block in the Implementation Blueprint.

---

## Agent Instructions

1. Work in the feature worktree (`parrot sdd worktree ensure --slug pbac-based-per-user-filter --feature-id FEAT-184`).
2. Confirm TASK-950 is done in `sdd/tasks/index/pbac-based-per-user-filter.json`.
3. Verify the Codebase Contract; implement from the blueprint; complete every `# FILL IN:`.
4. Run the Validation Commands; commit only the listed files.
5. Close with `parrot sdd task close TASK-951 pbac-based-per-user-filter verified` and fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (FEAT-184)
**Date**:
**Notes**: Seat: gpt-5.6-sol (codex) · Attempts: 1 · merged by engine. Validated: tests/auth/test_user_filter.py green.

**Deviations from spec**: none
