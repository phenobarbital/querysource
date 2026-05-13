# TASK-644: Implement Zammad `type: search` mode

**Feature**: NAV-8330-zammad-differential-tickets
**Spec**: `sdd/specs/NAV-8330-zammad-differential-tickets.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

The `zammad.py` provider currently fetches ALL tickets on every ETL run. This task
adds a `type: search` mode that accepts pre-resolved `firstdate`/`lastdate` conditions
from the task framework and uses the Zammad search endpoint to fetch only tickets
within that date range. The response format of the search endpoint differs from the
list endpoint and must be normalised to the same `list[dict]` shape.

Implements: Spec Section 2 (Architecture), Section 3 (Module 1), Section 4 (all unit tests).

---

## Scope

- In `__post_init__`: read and pop `type`, `firstdate`, `lastdate` from `self._conditions`;
  store `self._zammad_type`; for `type == "search"` build the search `base_url` with the
  date range embedded; raise `ValueError` for unknown `type` or missing `firstdate`.
- In `query()`: add a dispatch at the top — if `self._zammad_type == "search"`, return
  `await self._search_query()` immediately.
- New `_search_query()` async method: paginate the search endpoint, extract
  `list(result["assets"]["Ticket"].values())` per page, stop when
  `result.get("tickets_count", 0) == 0` or `result.get("tickets", [])` is empty,
  return accumulated `list[dict]`.
- Write all 8 unit tests listed in spec Section 4 in `tests/test_zammad_search.py`.

**NOT in scope**:
- Updating any YAML task files (separate repo).
- Changes to `tickets()` method.
- Changing pagination logic for `type: tickets` (list mode).
- Date calculation — dates arrive already formatted as strings.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/providers/sources/zammad.py` | MODIFY | Add type dispatch + `_search_query()` |
| `tests/test_zammad_search.py` | CREATE | 8 unit tests for search type |

---

## Codebase Contract (Anti-Hallucination)

> Use these VERBATIM. Do not invent alternatives.

### Verified Imports

```python
# Already in querysource/providers/sources/zammad.py (verified 2026-05-13)
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

### Existing Class Signatures to Extend

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
    ) -> None:
        # line 33-43: reads api_url from self._conditions or definition.params
        # line 44: self.base_url = '{api_url}/api/v1/tickets/?per_page=100&page={page}'
        # line 45: self._args['page'] = 1
        # line 48-58: reads api_token, sets self._headers['Authorization'] = f'Bearer {api_token}'

    async def tickets(self): ...   # line 61 — calls self.query(), do NOT change signature
    async def query(self): ...     # lines 65-86 — add dispatch at top ONLY, do not alter existing logic
```

```python
# querysource/providers/sources/abstract.py:120
def build_url(self, url, queryparams: str = None, args: dict = None) -> str:
    # formats url.format(**args), then optionally appends queryparams
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
)  # returns tuple: (result, error)
```

### Key Attributes (set in `__post_init__`, used in `query()`)

| Attribute | Type | Line set | Usage |
|---|---|---|---|
| `self.base_url` | `str` | 44 | URL template with `{api_url}` and `{page}` placeholders |
| `self._args['api_url']` | `str` | 34–43 | Resolved Zammad base URL |
| `self._args['page']` | `int` | 45 | Starts at 1; incremented per page in `query()` |
| `self._headers['Authorization']` | `str` | 59 | `Bearer <token>` |
| `self._result` | `list` | 66 | Accumulator initialised at the start of `query()` |

### Verified Search Response Shape

```python
# Confirmed by user against real Zammad instances (2026-05-13)
# GET /api/v1/tickets/search?query=updated_at:[firstdate TO lastdate]&per_page=100&page=N
{
    "tickets": [8080],          # list of int ticket IDs on this page
    "tickets_count": 1,         # count on THIS page (0 → stop paginating)
    "assets": {
        "Ticket": {
            "8080": {           # str(id) as key — NOT int
                "id": 8080,
                "group_id": 2,
                "title": "Kiosk failure",
                "updated_at": "2026-05-12T23:13:43.025Z",
                # ... all same fields as list endpoint
            }
        },
        "Group": {...},
        "User": {...},
        "Role": {...},
        "Organization": {...}
    }
}
# Normalisation target: list(result["assets"]["Ticket"].values())
# Empty / stop: result.get("tickets_count", 0) == 0  or  not result.get("tickets")
```

### Verified List Response Shape (reference, do not change)

```python
# GET /api/v1/tickets/?per_page=100&page=N → flat list
[{"id": 11, "group_id": 2, ...}, ...]
# Stop: result is empty list [] (handled by existing `elif not result: return self._result`)
```

### Does NOT Exist

- ~~`zammad._zammad_type`~~ — does not exist yet; created by this task in `__post_init__`
- ~~`zammad._conditions['type']`~~ — does not exist yet; to be read and popped
- ~~`zammad._conditions['hours_window']`~~ — NOT part of this feature; do not add it
- ~~`zammad_search` class~~ — do NOT create a subclass
- ~~`/api/v1/tickets/?updated_after=DATETIME`~~ — not a valid Zammad endpoint
- ~~`result["tickets_count"]` as total count~~ — it is the PAGE count, not total
- ~~`result["assets"]["Ticket"]` as a list~~ — it is a `dict` keyed by `str(id)`
- ~~`restSource.search_url`~~ — no such attribute on the base class

---

## Implementation Notes

### Pattern to Follow

Read and pop `type`/`firstdate`/`lastdate` exactly as `api_url` and `api_token` are
handled in the existing `__post_init__` (lines 33–58):

```python
# Example of the pattern already used (lines 33-43):
if 'api_url' in self._conditions:
    self._args['api_url'] = self._conditions['api_url']
    del self._conditions['api_url']
else:
    ...
```

### Date Format Warning

`firstdate` and `lastdate` arrive as `"%Y-%m-%d %H:%M:%S"` (e.g. `"2026-05-13 10:00:00"`).
Verify whether Zammad accepts this format or requires ISO 8601 (`T`/`Z`).
If needed, apply: `firstdate.replace(" ", "T") + "Z"` before embedding in the query string.

### Search URL Construction

The date range is embedded as a literal in `base_url` at `__post_init__` time
(not as a `{placeholder}` for `build_url`). Only `{api_url}` and `{page}` remain
as format placeholders:

```python
query_str = f"updated_at:[{firstdate} TO {lastdate}]"
self.base_url = (
    '{api_url}/api/v1/tickets/search'
    f'?query={urlencode({"query": query_str})[6:]}'  # or manual encode
    '&per_page=100&page={page}'
)
```

Alternatively build the query string manually to avoid urlencode complexity:

```python
import urllib.parse
encoded_query = urllib.parse.quote(f"updated_at:[{firstdate} TO {lastdate}]")
self.base_url = f'{{api_url}}/api/v1/tickets/search?query={encoded_query}&per_page=100&page={{page}}'
```

### `_search_query()` Loop

Mirror the structure of the existing `query()` but handle the search response shape:

```python
async def _search_query(self):
    self._result = []
    while True:
        self.url = self.build_url(self.base_url, args=self._args)
        result, error = await self.request(self.url, self.method, headers=self._headers)
        if error is not None:
            logging.error(f'Zammad Search Error: {error!s}')
        elif not result or not result.get('tickets'):
            return self._result
        else:
            if 'error' in result:
                return [result]
            tickets = list(result.get('assets', {}).get('Ticket', {}).values())
            if not tickets:
                return self._result
            self._args['page'] += 1
            self._result += tickets
```

### Key Constraints

- `__post_init__` must pop (not just read) `type`, `firstdate`, `lastdate` so they
  are not forwarded as HTTP parameters.
- `query()` existing logic must remain byte-for-byte identical except for the dispatch
  added at the very top.
- `self._zammad_type` defaults to `"tickets"` when `type` is not in conditions.

---

## Acceptance Criteria

- [ ] `type: search` + `firstdate`/`lastdate` → `base_url` contains `/tickets/search` with date-range query
- [ ] `type: tickets` (or absent) → `base_url` unchanged, `_zammad_type == "tickets"`
- [ ] `type`, `firstdate`, `lastdate` absent from `self._conditions` after `__post_init__`
- [ ] `_search_query()` returns `list[dict]` of ticket objects (from `assets.Ticket.values()`)
- [ ] Pagination stops on `tickets_count == 0` or empty `tickets`
- [ ] `type: search` without `firstdate` raises `ValueError`
- [ ] Unknown `type` raises `ValueError`
- [ ] All 8 unit tests pass: `pytest tests/test_zammad_search.py -v`
- [ ] Existing list-mode behavior unaffected (no regression)

---

## Test Specification

```python
# tests/test_zammad_search.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


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

BASE_CONDITIONS = {
    "api_url": "https://test.zammad.example/",
    "api_token": "test-token",
}


def make_zammad(extra_conditions):
    from querysource.providers.sources.zammad import zammad
    conditions = {**BASE_CONDITIONS, **extra_conditions}
    # Instantiate with minimal stubs
    with patch.object(zammad, '__init__', lambda self, *a, **kw: None):
        instance = zammad.__new__(zammad)
        instance._conditions = conditions
        instance._args = {}
        instance._headers = {}
        instance._env = MagicMock()
        instance._env.get = lambda k, fallback=None: fallback or k
        instance.logger = MagicMock()
        instance.__post_init__(definition=None, conditions=conditions)
    return instance


class TestZammadPostInit:
    def test_default_type_uses_list_endpoint(self):
        z = make_zammad({})
        assert '/api/v1/tickets/' in z.base_url
        assert z._zammad_type == 'tickets'

    def test_tickets_type_uses_list_endpoint(self):
        z = make_zammad({'type': 'tickets'})
        assert '/api/v1/tickets/' in z.base_url
        assert '/tickets/search' not in z.base_url

    def test_search_type_sets_search_url(self):
        z = make_zammad({
            'type': 'search',
            'firstdate': '2026-05-13 10:00:00',
            'lastdate': '2026-05-13 14:00:00',
        })
        assert '/tickets/search' in z.base_url
        assert 'updated_at' in z.base_url

    def test_type_firstdate_lastdate_popped_from_conditions(self):
        z = make_zammad({
            'type': 'search',
            'firstdate': '2026-05-13 10:00:00',
            'lastdate': '2026-05-13 14:00:00',
        })
        assert 'type' not in z._conditions
        assert 'firstdate' not in z._conditions
        assert 'lastdate' not in z._conditions

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError):
            make_zammad({'type': 'unknown_type'})

    def test_search_without_firstdate_raises(self):
        with pytest.raises(ValueError):
            make_zammad({'type': 'search', 'lastdate': '2026-05-13 14:00:00'})


class TestZammadSearchQuery:
    @pytest.mark.asyncio
    async def test_search_response_normalised(self):
        z = make_zammad({
            'type': 'search',
            'firstdate': '2026-05-13 10:00:00',
            'lastdate': '2026-05-13 14:00:00',
        })
        z.build_url = MagicMock(return_value='http://fake/search')
        z.request = AsyncMock(side_effect=[
            (SEARCH_RESPONSE_PAGE, None),
            (SEARCH_RESPONSE_EMPTY, None),
        ])
        result = await z._search_query()
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]['id'] == 8080

    @pytest.mark.asyncio
    async def test_search_stops_on_empty_tickets(self):
        z = make_zammad({
            'type': 'search',
            'firstdate': '2026-05-13 10:00:00',
            'lastdate': '2026-05-13 14:00:00',
        })
        z.build_url = MagicMock(return_value='http://fake/search')
        z.request = AsyncMock(return_value=(SEARCH_RESPONSE_EMPTY, None))
        result = await z._search_query()
        assert result == []
        z.request.assert_called_once()
```

---

## Agent Instructions

1. **Read the spec** at `sdd/specs/NAV-8330-zammad-differential-tickets.spec.md`
2. **No dependencies to check** — this is the first and only task
3. **Verify the Codebase Contract** before writing code:
   - Read `querysource/providers/sources/zammad.py` fully
   - Confirm `__post_init__` structure (lines 22–59) matches the contract above
   - Note date format: `firstdate`/`lastdate` arrive as `"%Y-%m-%d %H:%M:%S"`
4. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
5. **Implement** following scope and notes above
6. **Run tests**: `source .venv/bin/activate && pytest tests/test_zammad_search.py -v`
7. **Move file** to `sdd/tasks/completed/TASK-644-zammad-search-type.md`
8. **Update index** → `"done"`

---

## Completion Note

**Completed by**: Claude Sonnet 4.6
**Date**: 2026-05-13
**Notes**: Three runtime fixes beyond the original spec: (1) `type` must be read from `definition.params` — the task YAML does not pass `type` in conditions, it lives in the query slug params; (2) ISO date normalization guards against double-Z when the pattern framework already emits dates with Z suffix; (3) pagination stops early when `tickets_count < per_page` to avoid an unnecessary empty-page request.
**Deviations from spec**: added early-exit pagination (spec only described empty-response stop)
