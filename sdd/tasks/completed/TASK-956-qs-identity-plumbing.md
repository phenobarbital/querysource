# TASK-956: `QS` identity resolution and 403 propagation

**Feature**: FEAT-184 — PBAC-based per-user automatic filtering (`user_filtering`)
**Spec**: `sdd/specs/pbac-based-per-user-filter.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-951, TASK-955
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 4 (QS half), AC-13. `QS.build_provider()` resolves the
principal and raw userinfo once and hands both to the provider; a
`UserFilterDenied` must leave `build_provider()` as 403, not be wrapped into
`QueryError`.

---

## Scope

- Import `UserFilterDenied` in `qs.py`.
- After the `_pbac_session` extraction, compute `principal` (explicit or session-derived) and `userinfo`.
- Pass `"principal": principal` and `"userinfo": userinfo` in the slug provider `args`.
- Add `UserFilterDenied` to the re-raise tuple.
- Write `tests/test_qs_user_filtering.py` (unit + end-to-end through the provider/parser).

**NOT in scope**: `MultiQS` (follow-up); raw/query branches (`qs.py` `elif self._type in ('query', 'raw')`) — they keep passing what they pass today.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/qs.py` | MODIFY | Identity resolution, provider args, re-raise |
| `tests/test_qs_user_filtering.py` | CREATE | Unit + integration tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from ..exceptions import UserFilterDenied                         # created by TASK-950 (add to qs.py import block, lines 24-32)
from ..semantic.governance import principal_from_session          # verified: querysource/semantic/governance.py:17 — import lazily
from ..auth.user_filter import session_userinfo                   # created by TASK-951 — import lazily
from querysource.queries.qs import QS                             # verified: querysource/queries/qs.py:51
```

### Existing Signatures to Use
```python
# querysource/queries/qs.py
from ..exceptions import (DataNotFound, EmptySentence, ParserError, QueryAccessDenied, QueryError,
                          QueryException, RawQueryPlaceholderError,)      # lines 24-32 (QueryAccessDenied at 28)
class QS(BaseQuery):                                                   # line 51
    async def build_provider(self):
        _pbac_session = None                                           # line 201
        if self._request is not None: ... _cached = self._request.get('user_session', _SENTINEL)   # line 210
            if _cached is not _SENTINEL: _pbac_session = _cached        # lines 211-212
        except Exception: _pbac_session = None                          # lines 213-214
        if self._principal is not None and self._type != 'slug': ...    # line 216 (lazy import precedent at 217-218)
        args = {"slug": ..., "definition": objquery, "conditions": conditions, "request": self._request,
                "loop": self._loop, "principal": self._principal}       # lines 303-313 ("principal" at 312)
        self._qs = self._provider(**args); await self._qs.prepare_connection()   # lines 314-315
        except (RawQueryPlaceholderError, SemanticError, QueryAccessDenied): raise   # line 317
        except Exception as err: raise QueryError(..., code=400 if isinstance(err, ParserError) else 500)  # 319-326
# querysource/interfaces/queries.py:129 — AbstractQuery stores self._principal
# querysource/conf.py:466 QS_PBAC_ENABLED — must NOT gate user filtering (AC-4, test_qs_user_filtering_ignores_pbac_flag)
```

### Does NOT Exist
- ~~`QS._userinfo`~~ / ~~`QS.userinfo` kwarg~~ — not added (open question Q6: library callers get `userinfo={}`).
- ~~a `"userinfo"` key in provider args today~~ — added here.
- ~~replacing `self._principal`~~ — do not overwrite the stored attribute; use a local `principal`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/qs.py", "action": "MODIFY"},
    {"path": "tests/test_qs_user_filtering.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/qs.py#QS",
    "sym:querysource/semantic/governance.py#principal_from_session"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Add `UserFilterDenied` to the import block — *why*: needed by the re-raise tuple.
2. Resolve identity after the session try/except — *why*: once per call, reused by every provider (spec G3).
3. Update provider args and the re-raise tuple — *why*: AC-13.

### `querysource/queries/qs.py` (MODIFY — imports)
```python
# occurrences: 1 (verified: grep -c '    QueryAccessDenied,' querysource/queries/qs.py)
# AFTER — insert below `    RawQueryPlaceholderError,` inside the `from ..exceptions import (` block (qs.py:31):
    UserFilterDenied,
```

### `querysource/queries/qs.py` (MODIFY — identity resolution)
```python
# occurrences: 1 (verified: grep -c "                _cached = self._request.get('user_session', _SENTINEL)" querysource/queries/qs.py)
# Disambiguated anchor: insert AFTER the try/except that ends with
#             except Exception:
#                 _pbac_session = None
# (qs.py:213-214) and BEFORE `        if self._principal is not None and self._type != 'slug':` (qs.py:216):
        # FEAT-184: caller identity for per-user filtering, resolved once.
        from ..auth.user_filter import session_userinfo
        _user_principal = self._principal
        if _user_principal is None and _pbac_session is not None:
            from ..semantic.governance import principal_from_session
            _user_principal = principal_from_session(_pbac_session)
        _user_info = session_userinfo(_pbac_session)
```

### `querysource/queries/qs.py` (MODIFY — provider args)
```python
# occurrences: 1 (verified: grep -c '                    "principal": self._principal,' querysource/queries/qs.py)
# REPLACE line 312 with:
                    "principal": _user_principal,
                    "userinfo": _user_info,
```
**Why**: `semanticProvider` pops `principal` itself, and a session-derived principal is what it would compute anyway (providers/semantic.py:60-67).

### `querysource/queries/qs.py` (MODIFY — re-raise)
```python
# occurrences: 1 (verified: grep -c '            except (RawQueryPlaceholderError, SemanticError, QueryAccessDenied):' querysource/queries/qs.py)
# REPLACE line 317 with:
            except (RawQueryPlaceholderError, SemanticError, QueryAccessDenied, UserFilterDenied):
```

### `tests/test_qs_user_filtering.py` (CREATE)
```python
"""FEAT-184 TASK-956 — QS identity plumbing and 403 propagation."""
from __future__ import annotations

import pytest

from querysource.exceptions import UserFilterDenied

OPTIONS = {"rules": [{"column": "user_id", "attribute": "user_id"}], "program": {"restricted": True}}


# FILL IN: fixture building a QS(slug=...) whose definition repository returns a QueryModel with
#          user_filtering=True / OPTIONS, and a fake request mapping {"user_session": {"user": {...}}} —
#          follow the patching style of tests/handlers/test_queryservice_definition_forwarding.py
# FILL IN: test_qs_resolves_principal_from_session — provider args carry a QSPrincipal (user_id from session) and userinfo dict
# FILL IN: test_qs_reraises_user_filter_denied — session without the program → pytest.raises(UserFilterDenied), code 403
# FILL IN: test_qs_user_filtering_ignores_pbac_flag — monkeypatch QS_PBAC_ENABLED False, no session → UserFilterDenied
# FILL IN: test_user_filter_end_to_end_sql — two sessions with different user_id → rendered queries (qs._qs._query after
#          prepare_connection, or dry_run) contain each user's value and checksum() differs (AC-14)


def test_placeholder_import() -> None:
    assert UserFilterDenied().code == 403
```

### FILL IN checklist
- [ ] test fixture and four tests; remove `test_placeholder_import` once real tests exist

---

## Acceptance Criteria

- [ ] Spec AC-4 (end-to-end), AC-13, AC-14
- [ ] Existing QS/handler PBAC tests still pass
- [ ] `ruff check querysource/queries/qs.py tests/test_qs_user_filtering.py` clean

---

## Validation Commands

- `pytest tests/test_qs_user_filtering.py -q`
- `pytest tests/handlers/test_queryservice_pbac_smoke.py -q`
- `pytest tests/providers/test_semantic_provider.py -q`

---

## Test Specification

See the test block in the Implementation Blueprint.

---

## Agent Instructions

1. Work in the feature worktree (`parrot sdd worktree ensure --slug pbac-based-per-user-filter --feature-id FEAT-184`).
2. Confirm TASK-951 and TASK-955 are done; verify the Codebase Contract.
3. Implement from the blueprint; complete every `# FILL IN:`.
4. Run the Validation Commands; commit only the listed files.
5. Close with `parrot sdd task close TASK-956 pbac-based-per-user-filter verified` and fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (FEAT-184)
**Date**:
**Notes**: Seat: gpt-5.6-sol x2 then gpt-5.6-luna (attempt 3) · merged manually (engine task-tier validation reported 0 failures). Validated in feature worktree: test_qs_user_filtering + queryservice_pbac_smoke + semantic_provider -> 8 passed, 11 skipped.

**Deviations from spec**: none
