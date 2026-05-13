# Feature Specification: Zammad Differential Ticket Retrieval via updated_at

**Feature ID**: FEAT-092
**Date**: 2026-05-13
**Author**: wcabrera
**Status**: approved
**Jira**: NAV-8330
**Target version**: next

---

## 1. Motivation & Business Requirements

### Problem Statement

The `zammad.py` provider fetches **all** tickets on every ETL run via the paginated
list endpoint (`/api/v1/tickets/?per_page=100&page=N`). With large ticket histories
across four programs (TROC, Apple, Bose, Pokémon), this generates unnecessary API
calls, slow runtimes, and redundant BigQuery writes.

The ETL runs hourly; only tickets modified in a recent time window are relevant for
each incremental run.

### Goals

- Add a `type: search` mode to `zammad.py` that fetches only tickets within a date
  range supplied as `firstdate` / `lastdate` conditions.
- The provider **never** calculates dates — those arrive pre-resolved from the task
  framework's `pattern` interpolation.
- Normalize the search response to the same `list[dict]` format returned by the
  existing list endpoint, so downstream steps are unaffected.
- Keep `type: tickets` (the existing full-list mode) entirely unchanged.
- Require no new Python dependencies.

### Non-Goals (explicitly out of scope)

- Migrating the Pokémon client's SQL-based integration (`file_sql: pokemon_tickets.sql`).
- Creating a separate `zammad_search` provider class or new query slug in the DB.
- Date/time calculation inside the provider.
- Updating task YAML files (those live in `navigator-new/tasks`, a separate repo).

---

## 2. Architectural Design

### Overview

Two targeted changes to `querysource/providers/sources/zammad.py`:

1. **`__post_init__`**: reads `type`, `firstdate`, `lastdate` from `self._conditions`
   (popping them so they are not forwarded as HTTP params). For `type == "search"`,
   stores the dates and sets the search `base_url`. Stores `self._zammad_type` for
   dispatch.

2. **`query()`**: dispatches to `_search_query()` when `self._zammad_type == "search"`,
   otherwise runs the existing list logic unchanged. The new `_search_query()` method
   handles the different search response format and normalises it to `list[dict]`.

The task YAML resolves `firstdate`/`lastdate` via `pattern` before the provider runs:

```yaml
- QueryToPandas:
    query_slug: zammad_tickets
    conditions:
      api_url: ZAMMAD_TROC_INSTANCE
      api_token: ZAMMAD_TROC_TOKEN
      type: search
      pattern:
        firstdate:
          - date_diff
          - value: current_date
            diff: 4
            mode: hours
            mask: "%Y-%m-%d %H:%M:%S"
        lastdate:
          - today
          - mask: "%Y-%m-%d %H:%M:%S"
```

### Response Format Difference (Critical)

The two endpoints return fundamentally different JSON structures:

**List** (`/api/v1/tickets/`) → flat `list[dict]`:
```json
[
  {"id": 11, "group_id": 2, "title": "Assistance Form", ...},
  ...
]
```

**Search** (`/api/v1/tickets/search`) → nested wrapper:
```json
{
  "tickets": [8080],
  "tickets_count": 1,
  "assets": {
    "Ticket": {
      "8080": {"id": 8080, "group_id": 2, "title": "Kiosk failure", ...}
    },
    "Group": {...},
    "User": {...}
  }
}
```

`_search_query()` must extract `list(result["assets"]["Ticket"].values())` from each
page and accumulate them, so callers receive the same `list[dict]` as from the list
endpoint.

### Component Diagram

```
YAML task
  conditions: {type: search, firstdate: "2026-05-13 10:00:00", lastdate: "2026-05-13 14:00:00", ...}
       │
       ▼
zammad.__post_init__
  ├── type == "tickets" (default)
  │     self._zammad_type = "tickets"
  │     self.base_url = /api/v1/tickets/?per_page=100&page={page}   ← unchanged
  └── type == "search"
        self._zammad_type = "search"
        self.base_url = /api/v1/tickets/search
                        ?query=updated_at:[{firstdate} TO {lastdate}]
                        &per_page=100&page={page}
       │
       ▼
zammad.query()
  ├── _zammad_type == "tickets" → existing loop (list response, unchanged)
  └── _zammad_type == "search"  → _search_query()
                                   ├── per page: extract assets.Ticket.values()
                                   ├── stop when tickets_count == 0 or tickets == []
                                   └── return accumulated list[dict]  (same shape as list mode)
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `querysource/providers/sources/zammad.py` | modifies | `__post_init__` + `query()` dispatch + new `_search_query()` |
| `querysource/providers/sources/abstract.py` | uses | `build_url(url, args=self._args)` unchanged |
| `querysource/providers/sources/http.py` | uses | `self.request(url, method, headers)` unchanged |

### Data Models

No new data models. Both modes return `list[dict]` (raw Zammad ticket fields).

Conditions consumed and popped in `__post_init__`:

| Key | Type | Required for | Description |
|---|---|---|---|
| `type` | `str` | always | `"tickets"` (default) or `"search"` |
| `firstdate` | `str` | `search` only | Lower bound, formatted `"%Y-%m-%d %H:%M:%S"` |
| `lastdate` | `str` | `search` only | Upper bound, formatted `"%Y-%m-%d %H:%M:%S"` |

### New Internal Interfaces

```python
# querysource/providers/sources/zammad.py — new private method
async def _search_query(self) -> list[dict]: ...
```

No new public API.

---

## 3. Module Breakdown

### Module 1: Zammad Provider — Search Type

- **Path**: `querysource/providers/sources/zammad.py`
- **Responsibility**:
  - In `__post_init__`: read and pop `type`, `firstdate`, `lastdate` from
    `self._conditions`; store `self._zammad_type`; for `type == "search"`, URL-encode
    the date-range query and set `self.base_url` to the search endpoint.
  - In `query()`: add a dispatch at the top — if `self._zammad_type == "search"`,
    delegate to `_search_query()`.
  - New `_search_query()`: paginate the search endpoint; on each page extract
    `result["assets"]["Ticket"].values()` and append to `self._result`; stop when
    `result.get("tickets_count", 0) == 0` or `result.get("tickets", [])` is empty.
- **Depends on**: `urllib.parse.urlencode` (already imported), `restSource`

---

## 4. Test Specification

### Unit Tests

| Test | Description |
|---|---|
| `test_zammad_default_type_uses_list_endpoint` | No `type` in conditions → `base_url` is list endpoint, `_zammad_type == "tickets"` |
| `test_zammad_tickets_type_uses_list_endpoint` | `type: tickets` → same as above |
| `test_zammad_search_sets_search_url` | `type: search` with `firstdate`/`lastdate` → `base_url` contains `/tickets/search` and date range |
| `test_zammad_type_firstdate_lastdate_popped` | After `__post_init__`, `type`, `firstdate`, `lastdate` absent from `self._conditions` |
| `test_zammad_invalid_type_raises` | Unknown `type` → `ValueError` |
| `test_zammad_search_missing_firstdate_raises` | `type: search` without `firstdate` → `ValueError` |
| `test_zammad_search_response_normalised` | `_search_query()` given search-format page → returns flat `list[dict]` of ticket objects |
| `test_zammad_search_stops_on_empty_tickets` | Page with `tickets_count: 0` → loop stops |

### Integration Tests

| Test | Description |
|---|---|
| `test_zammad_search_live` | (Manual) Hit a real Zammad instance; verify returned tickets have `updated_at` within `[firstdate, lastdate]` |

### Test Data / Fixtures

```python
SEARCH_RESPONSE_PAGE = {
    "tickets": [8080],
    "tickets_count": 1,
    "assets": {
        "Ticket": {
            "8080": {
                "id": 8080,
                "group_id": 2,
                "title": "Kiosk failure",
                "updated_at": "2026-05-12T23:13:43.025Z",
            }
        }
    }
}

SEARCH_RESPONSE_EMPTY = {
    "tickets": [],
    "tickets_count": 0,
    "assets": {}
}

@pytest.fixture
def zammad_search_conditions():
    return {
        "api_url": "https://test.zammad.example/",
        "api_token": "test-token",
        "type": "search",
        "firstdate": "2026-05-13 10:00:00",
        "lastdate": "2026-05-13 14:00:00",
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

- [ ] `type: search` with `firstdate`/`lastdate` sets `base_url` to `/api/v1/tickets/search` with a date-range query.
- [ ] `type: tickets` (or no `type`) leaves `base_url` exactly as before — no behavioral change to existing mode.
- [ ] `type`, `firstdate`, `lastdate` are removed from `self._conditions` after `__post_init__`.
- [ ] Search response is normalised: `_search_query()` returns `list[dict]` of full ticket objects (from `assets.Ticket`), identical shape to the list endpoint output.
- [ ] Pagination stops when `tickets_count == 0` or `tickets == []`.
- [ ] `type: search` without `firstdate` raises `ValueError`.
- [ ] Unknown `type` raises `ValueError`.
- [ ] All unit tests pass (`pytest tests/ -v -k zammad`).
- [ ] No changes outside `zammad.py`.

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**

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

No new imports needed — `urlencode` is already present.

### Existing Class Signatures

```python
# querysource/providers/sources/zammad.py:14-86
class zammad(restSource):
    method: str = 'GET'    # line 19
    timeout: int = 60      # line 20

    def __post_init__(
        self,
        definition: dict = None,
        conditions: dict = None,
        request: Any = None,
        **kwargs
    ) -> None: ...          # lines 22-59 — extend this

    async def tickets(self): ...   # line 61 — calls self.query(), do NOT change
    async def query(self): ...     # lines 65-86 — add dispatch at top only
```

```python
# querysource/providers/sources/abstract.py:120
def build_url(self, url, queryparams: str = None, args: dict = None) -> str: ...
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
): ...  # returns (result, error)
```

### Key Attributes

| Attribute | Type | Line | Description |
|---|---|---|---|
| `self.base_url` | `str` | 44 | URL template with `{api_url}` and `{page}` |
| `self._args['api_url']` | `str` | 34–43 | Resolved Zammad base URL |
| `self._args['page']` | `int` | 45 | Starts at 1; incremented per page |
| `self._headers['Authorization']` | `str` | 59 | `Bearer <token>` |
| `self._result` | `list` | 66 | Accumulator initialised in `query()` |

### Verified Search Response Shape (user-provided, 2026-05-13)

```python
# Search endpoint: GET /api/v1/tickets/search?query=updated_at:[...] &per_page=100&page=N
# Returns:
{
    "tickets": [8080],          # list of ticket IDs on this page
    "tickets_count": 1,         # count on this page (0 → stop paginating)
    "assets": {
        "Ticket": {
            "8080": {           # full ticket dict keyed by str(id)
                "id": 8080,
                "group_id": 2,
                "updated_at": "2026-05-12T23:13:43.025Z",
                # ... all ticket fields identical to list endpoint
            }
        },
        "Group": {...},
        "User": {...},
        "Role": {...},
        "Organization": {...}
    }
}

# Normalisation: list(result["assets"]["Ticket"].values())
# produces same shape as the list endpoint's response items.
```

### Verified List Response Shape (user-provided, 2026-05-13)

```python
# List endpoint: GET /api/v1/tickets/?per_page=100&page=N
# Returns flat list:
[
    {"id": 11, "group_id": 2, "title": "Assistance Form", "updated_at": "...", ...},
    ...
]
# Empty list [] → stop paginating
```

### Integration Points

| New Code | Connects To | Via | Verified At |
|---|---|---|---|
| `__post_init__` (search branch) | `self.base_url` | attribute assignment | `zammad.py:44` |
| `query()` dispatch | `self._zammad_type` | instance attribute | new, set in `__post_init__` |
| `_search_query()` | `self.request()` | `http.py:781` | `http.py:781` |
| `_search_query()` normalisation | `result["assets"]["Ticket"].values()` | dict access | user-provided response sample |

### Does NOT Exist (Anti-Hallucination)

- ~~`zammad._zammad_type`~~ — does not exist yet; to be added in `__post_init__`
- ~~`zammad._conditions['type']`~~ — does not exist yet; to be added
- ~~`zammad._conditions['firstdate']`~~ — does not exist yet; to be added
- ~~`zammad._conditions['hours_window']`~~ — NOT part of this feature; dates come from task framework
- ~~`zammad_search`~~ — no subclass; do NOT create one
- ~~`/api/v1/tickets/?updated_after=DATETIME`~~ — not a valid Zammad endpoint
- ~~`result["tickets_count"]` as total count~~ — it is the **page** count, not total
- ~~`result["assets"]["Ticket"]` as a list~~ — it is a `dict` keyed by `str(ticket_id)`
- ~~`restSource.search_url`~~ — no such attribute on the base class

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- Pop `type`, `firstdate`, `lastdate` from `self._conditions` in `__post_init__`,
  exactly as `api_url` and `api_token` are already handled (lines 33–58).
- Store `self._zammad_type: str` as an instance attribute for `query()` dispatch.
- Build the search query string using `urlencode` (already imported) or plain string
  formatting — the date range syntax is `updated_at:[{firstdate} TO {lastdate}]`.
- The search `base_url` must keep `{api_url}` and `{page}` as the only `str.format`
  placeholders; the date range is a literal embedded at `__post_init__` time.
- In `_search_query()`, stop when `result.get("tickets_count", 0) == 0`
  OR `not result.get("tickets")`, whichever comes first.
- Raise `ValueError` for unknown `type` or `type == "search"` without `firstdate`.

### Known Risks / Gotchas

- **Date format in query string** — `firstdate`/`lastdate` arrive as
  `"%Y-%m-%d %H:%M:%S"` from the task framework. The Zammad search API may expect
  ISO 8601 with `T` separator and `Z` suffix. Verify during implementation by
  testing against a real instance. A simple `replace(" ", "T") + "Z"` conversion
  may be needed before embedding in the query string.
- **`assets.Ticket` keyed by string** — keys are `str(id)`, not `int`. Use
  `.values()` to extract ticket dicts; never index by integer key.
- **Pokémon client** (`datasource: zammad_poke`, `file_sql: pokemon_tickets.sql`) uses
  a different integration pattern and is NOT covered by this spec.

### External Dependencies

No new packages required.

---

## 8. Open Questions

- [x] **Zammad Search API syntax** — `GET /api/v1/tickets/search?query=updated_at:[FROM TO]` confirmed working for TROC, Apple, Bose, and Pokémon instances. `per_page` param also confirmed. — *Owner: wcabrera*
- [x] **YAML task update scope** — Out of scope for this ticket. — *Owner: wcabrera*
- [x] **Date format in query string** — Confirm whether `"%Y-%m-%d %H:%M:%S"` is accepted as-is or must be converted to ISO 8601 (`T`/`Z`) before embedding in the Zammad search query. — *Owner: wcabrera*: not work, but its out of scope, the formated date depends of the ETL.

---

## Worktree Strategy

- **Isolation**: `per-spec` — single worktree, sequential tasks.
- **Rationale**: Only `zammad.py` changes. No parallelism benefit.
- **Cross-feature dependencies**: none.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-13 | wcabrera | Initial draft from brainstorm NAV-8330 |
| 0.2 | 2026-05-13 | wcabrera | Corrected design: dates from task `pattern`, search response normalisation, `query()` dispatch added |
