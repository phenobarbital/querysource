# TASK-960: Keep user_filter_options validation detail out of the HTTP response

**Feature**: FEAT-184 — PBAC-based per-user automatic filtering (`user_filtering`)
**Spec**: `sdd/specs/pbac-based-per-user-filter.spec.md`
**Discovered-from**: `issue:4be97634adbe`
**Status**: done
**Priority**: high
**Estimated effort**: S (1-2h)
**Depends-on**: TASK-950, TASK-956
**Assigned-to**: unassigned

---

## Context

Ledger issue `issue:4be97634adbe` (bug/major): the validation detail of a bad
`user_filter_options` document reaches the HTTP caller verbatim. Two sites
compose the leak:

1. **The raise site.** TASK-950's blueprint maps a pydantic `ValidationError`
   to `ConfigError(f"Invalid user_filter_options: {err}")`. A pydantic
   `ValidationError` stringifies to the full report — field paths, the
   `input_value` that failed, and the model/class names — so a definition
   authored by an admin (column names, attribute paths, program slugs) is
   embedded in the exception message.

2. **The wrapping site.** `QS.query()` builds the provider inside a
   `try/except Exception` (`querysource/queries/qs.py:321-327`). The handful of
   exceptions meant to keep their own code are re-raised by the tuple at
   `qs.py:317` (`RawQueryPlaceholderError, SemanticError, QueryAccessDenied`);
   `ConfigError` is **not** in it, so it falls to the generic branch and is
   re-raised as
   `QueryError(f"Cannot Initialize the provider {self._provider}, error: {err}")`.
   That string is what handlers return as the response body — so it carries
   both the pydantic detail *and* the provider class repr.

`parse_user_filter_options` runs during parser construction (TASK-954's
`_user_filter_sync`, reached from `BaseProvider.__init__`), which is inside
exactly that `try` block — so every malformed-definition `ConfigError` on the
slug path takes this route today.

Fix: log the detail server-side at ERROR with the slug, raise a **fixed,
operator-facing** `ConfigError` message with no interpolated validation text,
and let `ConfigError` surface from `QS` with its own 500 instead of being
re-wrapped with the provider repr.

---

## Scope

- `querysource/auth/user_filter.py` (created by TASK-950): in
  `parse_user_filter_options`, log the `ValidationError` detail
  (`logger.error`, with `exc_info`) and raise a constant-message
  `ConfigError` — never an f-string carrying `err`.
- `querysource/queries/qs.py`: import `ConfigError` and add it to the
  pass-through `except` tuple of the **slug** branch so it keeps its own
  message and 500 code.
- Tests in `tests/test_qs_config_error_redaction.py`.

**NOT in scope**: changing `ConfigError`'s signature or status code
(`querysource/exceptions.py:33` — it takes `message` only and is always 500);
the `'query'/'raw'` branch (`qs.py:366-376`) and the `'driver'` branch
(`qs.py:416-422`) — both build their provider with `definition=None`, so no
`user_filter_options` document exists there to leak; the wording of other
`ConfigError` call sites elsewhere in the package; `UserFilterDenied`
messaging (TASK-950 owns it, and its reason codes are deliberately public).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/auth/user_filter.py` | MODIFY | Log detail, raise constant-message `ConfigError` |
| `querysource/queries/qs.py` | MODIFY | Import `ConfigError`; add to slug-branch pass-through tuple |
| `tests/test_qs_config_error_redaction.py` | CREATE | Unit tests for both sites |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from ..exceptions import (                      # querysource/queries/qs.py:24-32 (existing block)
    DataNotFound,
    EmptySentence,
    ParserError,
    QueryAccessDenied,
    QueryError,
    QueryException,
    RawQueryPlaceholderError,
)
from ..semantic.exceptions import SemanticError  # qs.py:35
from ..exceptions import ConfigError             # querysource/exceptions.py:33 — NOT yet imported in qs.py
from pydantic import ValidationError             # raised by UserFilterOptions.model_validate (TASK-950)
```

### Existing Signatures to Use
```python
# querysource/exceptions.py
class QueryException(Exception):                 # line 6
    default_code: int = 500                      # line 13
    def __init__(self, message: str, code: int | None = None, **kwargs)   # line 16
class ConfigError(QueryException):               # line 33
    def __init__(self, message: str = None)      # line 35 — message ONLY; code is hardcoded 500
class QueryError(QueryException):                # line 49
# querysource/queries/qs.py
except (RawQueryPlaceholderError, SemanticError, QueryAccessDenied):   # line 317 (occurrences: 1)
    raise                                                              # line 319
# querysource/auth/user_filter.py                (created by TASK-950)
def parse_user_filter_options(raw) -> UserFilterOptions
```

### Does NOT Exist
- ~~`ConfigError(message, code=...)`~~ — the subclass drops `code`; do not pass one.
- ~~a sanitiser/redaction helper in `querysource/exceptions.py`~~ — none; use a module-level constant string.
- ~~`ConfigError` in `qs.py`'s import block~~ — line 24-32 does not list it; add it in alphabetical position (before `DataNotFound`).

---

## Implementation Blueprint

### `querysource/auth/user_filter.py`
```python
# Module-level constant, next to the reason-code constants (TASK-950)
#: Fixed caller-facing text. The validation detail is logged, never returned:
#: it echoes admin-authored columns, attribute paths and failing input values.
INVALID_OPTIONS_MESSAGE: str = (
    "Invalid user_filter_options for this query definition; see server logs."
)
```
```python
# MODIFY — inside parse_user_filter_options(), replacing TASK-950's
# `ConfigError(f"Invalid user_filter_options: {err}")` FILL IN
    try:
        return UserFilterOptions.model_validate(dict(raw))
    except (ValidationError, ValueError, TypeError) as err:
        logger.error(
            "FEAT-184: invalid user_filter_options document: %s", err, exc_info=True
        )
        raise ConfigError(INVALID_OPTIONS_MESSAGE) from err
```
**Why**: `raise ... from err` keeps the chain for the server-side traceback
while the *message* — the only part handlers serialise — stays constant.

### `querysource/queries/qs.py`
```python
# MODIFY — the import block at lines 24-32
from ..exceptions import (
    ConfigError,
    DataNotFound,
    ...
)
```
```python
# MODIFY — line 317 (occurrences: 1)
            except (RawQueryPlaceholderError, SemanticError, QueryAccessDenied, ConfigError):
                # Operational, semantic, access and configuration errors surface
                # with their own code — re-wrapping a ConfigError would splice its
                # message (and the provider repr) into the caller's response.
                raise
```
**Why**: the generic branch at `qs.py:321-327` interpolates `{err}` into a
`QueryError` that is returned to the client; a pass-through keeps the fixed
message and the 500 that `ConfigError` already sets.

### `tests/test_qs_config_error_redaction.py`
- FILL IN: `parse_user_filter_options` with an unknown key / bad column grammar
  → `ConfigError`; assert `str(exc.value) == INVALID_OPTIONS_MESSAGE` and that
  the raw offending value does **not** appear in `str(exc.value)`.
- FILL IN: assert the detail IS logged — `caplog` at ERROR contains the
  offending field name (detail goes to logs, not to the caller).
- FILL IN: `ConfigError` raised from provider construction inside `QS.query()`
  propagates unchanged (same type, same message, `code == 500`) and is not
  re-wrapped as `QueryError` — patch the resolved provider to raise
  `ConfigError(INVALID_OPTIONS_MESSAGE)`, mirroring the provider/loop fixture
  pattern of `tests/test_qs_user_filtering.py` (TASK-956).
- FILL IN: a non-`ConfigError` failure in the same block still becomes
  `QueryError` (no regression to the generic branch).

### Steps (in order)
1. Confirm TASK-950 is merged (`parse_user_filter_options` exists) and TASK-956
   is merged (the `QS` slug path carries identity) — this edits both.
2. Apply the `user_filter.py` constant + raise block.
3. Apply the two `qs.py` blocks.
4. Write the tests; run Validation Commands; commit only listed files.

### FILL IN checklist
- [ ] Exact pydantic exception set raised by `UserFilterOptions.model_validate` as TASK-950 lands it
- [ ] Logger handle in `user_filter.py` (module logger vs. passed-in) per TASK-950's final shape
- [ ] Provider-patching fixture form for the `QS.query()` test

---

## Acceptance Criteria
- [ ] A malformed `user_filter_options` document produces a `ConfigError` whose message is the fixed `INVALID_OPTIONS_MESSAGE` — no field paths, no input values, no pydantic text.
- [ ] The full validation detail is written to the server log at ERROR.
- [ ] `ConfigError` raised during provider construction reaches the handler as `ConfigError` (code 500), not as `QueryError("Cannot Initialize the provider ...")`.
- [ ] Non-`ConfigError` provider-init failures are still wrapped as `QueryError` with the existing 400/500 rule (spec AC-8 unaffected).

## Validation Commands
- `pytest tests/test_qs_config_error_redaction.py -q`
- `pytest tests/auth/test_user_filter_options.py -q`
- `pytest tests/test_qs_user_filtering.py -q`
- `ruff check querysource/auth/user_filter.py querysource/queries/qs.py tests/test_qs_config_error_redaction.py`

## Complexity Contract
```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/auth/user_filter.py", "action": "MODIFY"},
    {"path": "querysource/queries/qs.py", "action": "MODIFY"},
    {"path": "tests/test_qs_config_error_redaction.py", "action": "CREATE"}
  ],
  "contract_symbols": ["sym:querysource/queries/qs.py#QS.query", "sym:querysource/exceptions.py#ConfigError"]
}
```

## Completion Note
**Completed by**: Claude (sdd-start, direct)
**Date**: 2026-11-03
**Notes**: parse_user_filter_options logs the validation detail at ERROR and raises ConfigError(INVALID_OPTIONS_MESSAGE); ConfigError added to the slug-branch pass-through in QS.build_provider. 88 tests pass across the redaction, options, qs, user_filter and value-type suites. Closes issue:4be97634adbe on /sdd-done.

**Deviations from spec**: none. Test imports the TASK-956 helpers via the bare module name because tests/ is not a package.
