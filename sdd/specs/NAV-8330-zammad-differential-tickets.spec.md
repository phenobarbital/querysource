# Feature Specification: Zammad Differential Ticket Retrieval via updated_at

**Feature ID**: FEAT-092
**Date**: 2026-05-13
**Author**: wcabrera
**Status**: draft
**Jira**: NAV-8330
**Target version**: next

---

## 1. Motivation & Business Requirements

### Problem Statement

The `zammad.py` provider fetches **all** tickets on every ETL run via the paginated
list endpoint (`/api/v1/tickets/?per_page=100&page=N`). With large ticket histories
across four programs (TROC, Apple, Bose, Pokémon), this generates unnecessary API
calls, slow runtimes, and redundant BigQuery writes.

The ETL runs hourly; only tickets modified in the last ~4 hours are relevant for each
incremental run.

### Goals

- Add a `type: search` mode to the `zammad` provider that fetches only tickets
  modified after `now() - hours_window`.
- Keep `type: tickets` (the existing full-list mode) entirely unchanged.
- Make `hours_window` configurable per query slug instance via `conditions`.
- Require no new Python dependencies.

### Non-Goals (explicitly out of scope)

- Migrating the Pokémon client's SQL-based integration (`file_sql: pokemon_tickets.sql`).
- Creating a separate `zammad_search` provider class or a new query slug in the DB.
- Adding a scheduling or ETL-run-log table (timestamps come from `now()` at runtime).
- Changing the task YAML file itself (that lives in `navigator-new/tasks`, a separate repo).

---

## 2. Architectural Design

### Overview

Extend `zammad.__post_init__` to read a `type` key from `self._conditions`.

- `type == "tickets"` (default): current behavior — list endpoint, no date filter.
- `type == "search"`: compute `cutoff_dt = datetime.now(UTC) - timedelta(hours=hours_window)`,
  format as ISO 8601, and set `self.base_url` to the Zammad search endpoint with
  an `updated_at:>cutoff_dt` query filter.

The existing `query()` pagination loop is unchanged; it terminates naturally when the
API returns an empty page.

### Component Diagram

```
YAML task (conditions: type=search, hours_window=4, api_url=..., api_token=...)
        │
        ▼
zammad.__post_init__
  ├── type == "tickets" ──→ base_url = /api/v1/tickets/?per_page=100&page={page}  (no change)
  └── type == "search"  ──→ cutoff_dt = now() - timedelta(hours=hours_window)
                            base_url = /api/v1/tickets/search?query=updated_at:>{cutoff_iso}
                                        &per_page=100&page={page}
        │
        ▼
zammad.query()  ──→ paginate until empty page  ──→  return list[dict]
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `querysource/providers/sources/zammad.py` | modifies | Add type dispatch in `__post_init__` |
| `querysource/providers/sources/abstract.py` | uses | `build_url(url, args=self._args)` unchanged |
| `querysource/providers/sources/http.py` | uses | `self.request(url, method, headers)` unchanged |

### Data Models

No new data models. The provider returns `list[dict]` (raw Zammad ticket JSON),
same as today.

Relevant `conditions` keys consumed and popped in `__post_init__`:

| Key | Type | Default | Description |
|---|---|---|---|
| `type` | `str` | `"tickets"` | `"tickets"` or `"search"` |
| `hours_window` | `int` | `4` | Look-back window in hours for `type: search` |

### New Public Interfaces

```python
# querysource/providers/sources/zammad.py
class zammad(restSource):
    # New behavior — no signature change, no new public methods.
    # Controlled entirely via conditions dict passed at call time.
```

---

## 3. Module Breakdown

### Module 1: Zammad Provider — Type Dispatch

- **Path**: `querysource/providers/sources/zammad.py`
- **Responsibility**: Read `type` and `hours_window` from `self._conditions` in
  `__post_init__`. For `type == "search"`, calculate the UTC cutoff datetime and set
  `self.base_url` to the search endpoint URL. For `type == "tickets"` or missing `type`,
  set `self.base_url` to the existing list endpoint (no behavioral change).
- **Depends on**: `datetime`, `pytz` (both already imported), `restSource` (base class)

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_zammad_default_type_uses_list_endpoint` | Module 1 | No `type` in conditions → `base_url` is the list endpoint |
| `test_zammad_tickets_type_uses_list_endpoint` | Module 1 | `type: tickets` → `base_url` is the list endpoint |
| `test_zammad_search_type_sets_cutoff_url` | Module 1 | `type: search, hours_window: 4` → `base_url` contains `updated_at:>` and correct ISO datetime |
| `test_zammad_hours_window_custom` | Module 1 | `hours_window: 2` → cutoff is `now - 2h` (within tolerance) |
| `test_zammad_invalid_type_raises` | Module 1 | Unknown `type` → `ValueError` |
| `test_zammad_zero_hours_window_raises` | Module 1 | `hours_window: 0` → `ValueError` |
| `test_zammad_type_and_hours_popped_from_conditions` | Module 1 | After `__post_init__`, `type` and `hours_window` are absent from `self._conditions` |

### Integration Tests

| Test | Description |
|---|---|
| `test_zammad_search_live` | (Manual / staging) Hit a real Zammad instance with `type: search`; verify only recent tickets returned |

### Test Data / Fixtures

```python
@pytest.fixture
def zammad_search_conditions():
    return {
        "api_url": "https://test.zammad.example/",
        "api_token": "test-token",
        "type": "search",
        "hours_window": 4,
    }

@pytest.fixture
def zammad_tickets_conditions():
    return {
        "api_url": "https://test.zammad.example/",
        "api_token": "test-token",
    }
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] `type: search` with `hours_window: N` causes `base_url` to use `/api/v1/tickets/search?query=updated_at:>DATETIME`.
- [ ] The cutoff datetime equals `now(UTC) - timedelta(hours=N)` (within 5 seconds tolerance in tests).
- [ ] `type: tickets` (or no `type`) preserves the existing `base_url` exactly — no behavioral change.
- [ ] `type` and `hours_window` are removed from `self._conditions` so they are not passed as HTTP parameters.
- [ ] Unknown `type` values raise `ValueError` with a clear message.
- [ ] `hours_window <= 0` raises `ValueError`.
- [ ] All unit tests pass (`pytest tests/ -v -k zammad`).
- [ ] No changes to the existing `query()` loop — diff must show only `__post_init__` touched.
- [ ] Verified against at least one Zammad instance that the search endpoint returns only tickets
      with `updated_at >= cutoff_dt` (manual test — see Open Questions).

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> Implementation agents MUST NOT reference imports, attributes, or methods
> not listed here without first verifying they exist via `grep` or `read`.

### Verified Imports

```python
# Already in querysource/providers/sources/zammad.py (lines 1-10, verified 2026-05-13)
from typing import Any
from datetime import datetime
from urllib.parse import urlencode
import pytz
import urllib3
from navconfig.logging import logging
from asyncdb.exceptions import ProviderError, NoDataFound
from ..sources import restSource
from ...exceptions import DataNotFound
```

New import required for `timedelta`:
```python
from datetime import datetime, timedelta  # add timedelta to existing datetime import
```

### Existing Class Signatures

```python
# querysource/providers/sources/zammad.py:14
class zammad(restSource):
    method: str = 'GET'         # line 19
    timeout: int = 60           # line 20

    def __post_init__(
        self,
        definition: dict = None,
        conditions: dict = None,
        request: Any = None,
        **kwargs
    ) -> None: ...               # lines 22-59 — THIS is the method to extend

    async def tickets(self): ... # line 61
    async def query(self): ...   # lines 65-86 — DO NOT MODIFY
```

```python
# querysource/providers/sources/abstract.py:120
def build_url(self, url, queryparams: str = None, args: dict = None) -> str:
    # formats url.format(**args), optionally appends queryparams
    ...
```

```python
# querysource/providers/sources/http.py:781
async def request(
    self,
    url,
    method: str = 'get',
    data: dict = None,
    cookies: dict = None,
    headers: Optional[Union[dict, None]] = None
): ...
```

### Key Attributes Set in `__post_init__`

| Attribute | Type | Set at line | Description |
|---|---|---|---|
| `self.base_url` | `str` | 44 | URL template with `{api_url}` and `{page}` placeholders |
| `self._args['api_url']` | `str` | 34–43 | Resolved Zammad instance URL |
| `self._args['page']` | `int` | 45 | Starts at 1; incremented by `query()` |
| `self._headers['Authorization']` | `str` | 59 | `Bearer <token>` |
| `self._conditions` | `dict` | (from base) | Caller-supplied overrides; `api_url` and `api_token` are popped here |

### How `base_url` Is Used

```python
# querysource/providers/sources/zammad.py:68-69 (inside query())
self.url = self.build_url(
    self.base_url,
    args=self._args          # formats {api_url} and {page} placeholders
)
```

The search base_url must use the same `{api_url}` and `{page}` placeholders so
`build_url` can format it identically.

### Integration Points

| New Code | Connects To | Via | Verified At |
|---|---|---|---|
| `zammad.__post_init__` (search branch) | `self.base_url` | attribute set | `zammad.py:44` |
| `zammad.query()` | `self.base_url` | `build_url(self.base_url, args=self._args)` | `zammad.py:68` |
| cutoff calculation | `datetime`, `timedelta`, `pytz.UTC` | stdlib + already imported | `zammad.py:2,4` |

### Does NOT Exist (Anti-Hallucination)

- ~~`zammad.type`~~ — no `type` class attribute; must be read from `self._conditions`
- ~~`zammad._conditions['type']`~~ — does not exist yet; to be added as part of this feature
- ~~`zammad._conditions['hours_window']`~~ — does not exist yet; to be added
- ~~`zammad_search`~~ — no subclass; do NOT create one
- ~~`/api/v1/tickets/?updated_after=DATETIME`~~ — NOT a confirmed Zammad endpoint; do not use
- ~~`restSource.search_url`~~ — no such attribute on the base class
- ~~`self._args['cutoff_dt']`~~ — cutoff is embedded in the URL string, NOT passed as a separate `_args` key

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- Read and pop `type` and `hours_window` from `self._conditions` in `__post_init__`,
  exactly as `api_url` and `api_token` are already handled (lines 33–58).
- Use `pytz.UTC` for timezone-aware `datetime.now()` to produce a correct ISO 8601 UTC string.
- Format cutoff: `cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")` — no microseconds.
- The search `base_url` must keep `{api_url}` and `{page}` as the only format placeholders
  (cutoff datetime is a literal, not a placeholder, since it is fixed at `__post_init__` time).
- Raise `ValueError` (not a custom exception) for invalid `type` or `hours_window <= 0`.

### Known Risks / Gotchas

- **Zammad Search API syntax unverified** — the query `updated_at:>DATETIME` is the
  expected Elasticsearch-style syntax for Zammad, but must be confirmed against the
  actual instances before implementation. See Open Questions.
- **Search endpoint pagination param** — `/api/v1/tickets/search` may use `limit` instead
  of `per_page`. Verify before hard-coding `per_page=100` in the search URL.
- **Pokémon client** (`datasource: zammad_poke`, `file_sql: pokemon_tickets.sql`) uses a
  different integration pattern and is NOT covered by this spec.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `pytz` | already in venv | UTC-aware datetime for ISO 8601 cutoff |
| `datetime` (stdlib) | 3.11 | `timedelta` for window calculation |

No new packages required.

---

## 8. Open Questions

- [ ] **Zammad Search API syntax** — Confirm `GET /api/v1/tickets/search?query=updated_at:>DATETIME` works on TROC, Apple, and Bose Zammad instances. Check if Elasticsearch is enabled. — *Owner: wcabrera*
- [ ] **Search pagination param** — Does `/api/v1/tickets/search` accept `per_page=N` or `limit=N`? The list endpoint uses `per_page`; verify for search. — *Owner: wcabrera*
- [ ] **YAML task update** — Updating `troc_dhw_tickets.yaml` to add `type: search` + `hours_window` is in `navigator-new/tasks` (separate repo). Confirm this is in scope for NAV-8330 or a follow-up ticket. — *Owner: wcabrera*

---

## Worktree Strategy

- **Isolation**: `per-spec` — single worktree, sequential tasks.
- **Rationale**: Only one file changes (`zammad.py`). No parallelism benefit.
- **Cross-feature dependencies**: none — this feature is self-contained.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-13 | wcabrera | Initial draft from brainstorm NAV-8330 |
