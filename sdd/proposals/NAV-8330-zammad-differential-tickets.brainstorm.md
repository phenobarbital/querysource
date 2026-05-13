---
jira: NAV-8330
jira_summary: "Modify the queryslug for zammad ticket retrieval to use updated_at"
jira_type: Story
jira_priority: (not specified)
jira_components: [Zammad]
complexity: simple
status: exploration
---

# Brainstorm: Zammad Differential Ticket Retrieval via updated_at

**Date**: 2026-05-13
**Author**: wcabrera
**Status**: exploration
**Recommended Option**: A

---

## Problem Statement

The `zammad.py` provider currently fetches **all** tickets from every configured Zammad
instance on every ETL run, using the paginated list endpoint
(`/api/v1/tickets/?per_page=100&page=N`). With large ticket histories across four
programs (TROC, Apple, Bose, Pokémon), this causes excessive API calls, slow ETL
runtimes, and unnecessary load on the Zammad instances and downstream BigQuery writes.

The ETL (`troc_dhw_tickets.yaml`) runs hourly. Only tickets modified in the last ~4
hours are relevant for each incremental run; everything else is redundant data.

---

## Constraints & Requirements

- The existing `type: tickets` query slug behavior must remain unchanged (full/historical loads).
- The new `type: search` mode must calculate the cutoff datetime as `now() - N hours` at runtime.
- `N` (the window in hours) must be configurable in the query slug conditions (default: 4).
- All four program instances (TROC, Apple, Bose, Pokémon) use the same `zammad.py` provider.
  The fix must be a single change to that file.
- The YAML task (`troc_dhw_tickets.yaml`) passes credentials via `conditions` (e.g.,
  `api_url: ZAMMAD_TROC_INSTANCE`). The new `hours_window` param follows the same pattern.
- The exact Zammad search API endpoint must be verified before implementation
  (see Open Questions).
- No new Python dependencies required — `datetime` and `pytz` are already imported.

---

## Options Explored

### Option A: Type Dispatch in `__post_init__` — Zammad Search API

Add `type` dispatch to the existing `zammad.__post_init__`. When `type == "search"`,
calculate a cutoff datetime (`now - hours_window`) and build the search URL
(`/api/v1/tickets/search?query=updated_at:>DATETIME&per_page=100&page=N`).
The `type == "tickets"` path stays entirely unchanged.

A caller passes `conditions: {type: "search", hours_window: 4, api_url: ..., api_token: ...}`
in the YAML step. The provider reads `type` and `hours_window` from `self._conditions`,
removes them from conditions (so they aren't passed as HTTP params), and configures
`self.base_url` accordingly before the existing `query()` loop runs.

✅ **Pros:**
- Single class, single file — no new abstractions.
- Fully backward-compatible: existing slugs with `type: tickets` (or no type) are unchanged.
- `hours_window` is a plain condition, same pattern as `api_url`/`api_token` already in use.
- No changes required to YAML task structure, only add `type: search` + `hours_window`.

❌ **Cons:**
- Two behavioral branches inside one class; a reader must understand both modes.
- Requires the Zammad search API to support `updated_at` range queries (needs verification).

📊 **Effort:** Low

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `datetime` (stdlib) | Calculate `now() - timedelta(hours=N)` | Already imported in `zammad.py` |
| `pytz` (already imported) | UTC-aware datetime for ISO 8601 formatting | Already in scope |

🔗 **Existing Code to Reuse:**
- `querysource/providers/sources/zammad.py` — extend `__post_init__` and add a `_build_search_url()` helper.
- `querysource/providers/sources/abstract.py:120` — `build_url(url, queryparams, args)` to format the final URL.

---

### Option B: Subclass `zammad_search(zammad)`

Create a new class `zammad_search` that inherits from `zammad` and overrides only
`__post_init__` to configure the search URL. Register it as a separate provider
in the provider registry so callers use `source: zammad_search` in their query slugs.

✅ **Pros:**
- Clear separation of concerns — existing class untouched.
- Easier to test each mode in isolation.

❌ **Cons:**
- Requires a new provider name (`zammad_search`) in the registry and potentially a
  new query slug entry in the database, which is more deployment overhead.
- Violates the user's stated design preference ("add a new `type: search`" within
  the same provider).
- Duplicates credential-handling logic unless carefully placed in the base class.

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `datetime` (stdlib) | Cutoff calculation | Already available |

🔗 **Existing Code to Reuse:**
- `querysource/providers/sources/zammad.py:zammad` — base class to subclass.

---

### Option C: Client-Side Page-Stop Filtering (No Search API)

Keep the existing list endpoint but add logic to `query()` to stop paginating once
all tickets on a page have `updated_at` older than `now - hours_window`. Apply
a post-fetch filter to drop tickets outside the window.

This avoids dependency on the Zammad search API entirely.

✅ **Pros:**
- Does not require knowledge of the Zammad search endpoint.
- Useful fallback if Zammad instances do not have Elasticsearch enabled.

❌ **Cons:**
- Assumes the list endpoint returns tickets in descending `updated_at` order —
  **this is not guaranteed** by the Zammad API for the standard list endpoint.
  If order is by `id` (creation), the ETL may still fetch all tickets.
- Even in the best case, fetches more data than necessary (full pages until cutoff).
- More complex stopping logic inside `query()`.

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `datetime` (stdlib) | Cutoff comparison | Already available |

🔗 **Existing Code to Reuse:**
- `querysource/providers/sources/zammad.py:zammad.query()` — pagination loop to extend.

---

## Recommendation

**Option A** is recommended because it directly implements the design the user described
("maintain `type: tickets` for historical loads, add `type: search` for differential"),
requires the smallest diff, introduces no new files or provider registrations, and
is consistent with how the provider already handles per-instance credential overrides
via `conditions`.

The main risk — that the Zammad search API syntax needs verification — is an acceptable
blocker for the implementation task but not for design approval.

**AC Coverage:**
| Jira Acceptance Criterion | Covered by Option A |
|---|---|
| Only pull tickets modified in the last N hours | ✅ `updated_at:>cutoff_dt` in search URL |
| ETL runs hourly with a 4-hour window | ✅ `hours_window` condition (default 4) |
| All ticket ETLs in programs updated | ✅ Single `zammad.py` change covers all instances |
| Historical/full loads still work | ✅ `type: tickets` path untouched |

---

## Feature Description

### User-Facing Behavior

An ETL author adds `type: search` and optionally `hours_window: 4` to the `conditions`
block of any `QueryToPandas` step that uses `query_slug: zammad_tickets`.
The step then fetches only tickets modified in the last `hours_window` hours instead
of all tickets.

```yaml
- QueryToPandas:
    query_slug: zammad_tickets
    conditions:
      api_url: ZAMMAD_TROC_INSTANCE
      api_token: ZAMMAD_TROC_TOKEN
      type: search
      hours_window: 4
```

Omitting `type` (or using `type: tickets`) preserves the existing full-load behavior.

### Internal Behavior

1. `zammad.__post_init__` reads and pops `type` from `self._conditions`
   (default: `"tickets"`) and `hours_window` (default: `4`).
2. If `type == "tickets"`: `self.base_url` is set as today (no change).
3. If `type == "search"`:
   - Calculate `cutoff_dt = datetime.now(tz=pytz.UTC) - timedelta(hours=hours_window)`.
   - Format `cutoff_iso = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")`.
   - Set `self.base_url = '{api_url}/api/v1/tickets/search?query=updated_at:>{cutoff_iso}&per_page=100&page={page}'`.
4. The existing `query()` pagination loop runs unchanged — it terminates when the
   API returns an empty page, exactly as before.

### Edge Cases & Error Handling

- **`hours_window` is 0 or negative**: Raise `ValueError` early in `__post_init__`.
- **Zammad search returns `{"error": "..."}` instead of a list**: Existing
  `if 'error' in result: return [result]` guard in `query()` already handles this.
- **Empty result set** (no tickets updated in window): `query()` returns `[]`,
  downstream steps receive an empty DataFrame — already handled by the task framework.
- **Unknown `type` value**: Raise `ValueError` with a clear message listing valid types.

---

## Capabilities

### New Capabilities
- `zammad-differential-search`: Fetch only recently-modified Zammad tickets using
  the search API with an `updated_at` cutoff, configurable per query slug instance.

### Modified Capabilities
- `zammad-ticket-list`: Existing full-list behavior is preserved and unchanged.

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `querysource/providers/sources/zammad.py` | modifies | New type dispatch in `__post_init__`; `query()` loop unchanged |
| `tasks/programs/troc/tasks/troc_dhw_tickets.yaml` | extends | Add `type: search` + `hours_window` to each `QueryToPandas` step that should be differential |
| `zammad_tickets` query slug (DB) | extends | May optionally set `type: search` + `hours_window` at the slug level so YAML overrides aren't needed |

---

## Code Context

### Verified Codebase References

#### Classes & Signatures

```python
# From querysource/providers/sources/zammad.py:14
class zammad(restSource):
    method: str = 'GET'
    timeout: int = 60

    def __post_init__(
        self,
        definition: dict = None,
        conditions: dict = None,
        request: Any = None,
        **kwargs
    ) -> None: ...  # lines 22-59

    async def tickets(self): ...  # line 61

    async def query(self): ...   # lines 65-86
```

```python
# From querysource/providers/sources/abstract.py:120
def build_url(self, url, queryparams: str = None, args: dict = None) -> str:
    # formats url with args dict, appends queryparams
    ...
```

```python
# From querysource/providers/sources/zammad.py:44
self.base_url = '{api_url}/api/v1/tickets/?per_page=100&page={page}'
self._args['page'] = 1
```

#### Verified Imports

```python
# Already in querysource/providers/sources/zammad.py:1-10
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

#### Key Attributes & Constants

- `zammad._args` → `dict` used as format kwargs in `build_url` (e.g., `api_url`, `page`)
- `zammad._conditions` → `dict` of caller-supplied overrides (e.g., `api_url`, `api_token`)
- `zammad._headers['Authorization']` → Bearer token set in `__post_init__`
- `baseSource._env` → `navconfig.config` used to resolve env var names

### YAML Task Structure (User-Provided)

```yaml
# Source: tasks/programs/troc/tasks/troc_dhw_tickets.yaml
- QueryToPandas:                          # step 1
    query_slug: zammad_tickets
    conditions:
      api_url: ZAMMAD_TROC_INSTANCE
      api_token: ZAMMAD_TROC_TOKEN
```

### Does NOT Exist (Anti-Hallucination)

- ~~`zammad.type`~~ — no `type` attribute currently; must be read from `self._conditions`
- ~~`zammad._conditions['hours_window']`~~ — does not exist yet; to be added
- ~~`zammad_search`~~ — no subclass or separate provider currently exists
- ~~`/api/v1/tickets/?updated_after=DATETIME`~~ — not a confirmed Zammad endpoint;
  do NOT assume the list endpoint accepts a date filter parameter

---

## Open Questions

- [x] **Zammad Search API syntax** — Verify that `GET /api/v1/tickets/search?query=updated_at:>DATETIME` is the correct endpoint and query format for all four Zammad instances (TROC, Apple, Bose, Pokémon). Check if Elasticsearch is enabled on each instance. — *Owner: wcabrera*: the correct endpoint is like `GET /api/v1/tickets/search?query=updated_at:[2026-05-10T12:00:00.000Z TO 2026-05-13T00:00:00.000Z]` and its works for TROC, Apple, Bose and Pokemon.
- [x] **Search endpoint pagination** — Confirm whether `/api/v1/tickets/search` uses `per_page` or `limit` for page size. The list endpoint uses `per_page`; search endpoints sometimes differ. — *Owner: wcabrera*: the search endpoint use `per_page` and `page`, its works.
- [x] **Pokémon instance** — The Pokémon client uses `datasource: zammad_poke` with `file_sql: pokemon_tickets.sql` in the YAML (step 13), which is a different integration pattern. Confirm whether it also needs the differential filter or is out of scope. — *Owner: wcabrera*: its out of scope.
- [x] **YAML update scope** — Is updating `troc_dhw_tickets.yaml` in scope for this ticket, or only the provider? The YAML lives in `navigator-new/tasks`, a different repo/directory. — *Owner: wcabrera*: its out of scope.
