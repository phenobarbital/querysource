# TASK-625: QueryManager.get() List-Pagination Branch

**Feature**: querysource-slug-list-pagination
**Feature ID**: FEAT-090
**Spec**: `sdd/specs/querysource-slug-list-pagination.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-624
**Assigned-to**: unassigned

---

## Context

With the pagination models + SQL builders in place (TASK-624), this task wires
them into the live HTTP handler. It adds a **list-pagination branch** to
`QueryManager.get()` that activates when the caller is asking for the slug list
(no `{slug}` URL match, no `:meta` / `:insert` suffix).

Implements spec Section 3 Module 3 (`QueryManager.get()` list branch).

The three other branches of `get()` — single-slug, `:meta`, `:insert` — must
remain byte-for-byte equivalent in behaviour. Other verbs (`patch`, `put`,
`post`, `delete`) are not touched.

---

## Scope

- Modify `querysource/handlers/manager.py::QueryManager.get` to:
  1. After parsing params + detecting that **no slug and no meta suffix** are
     present, delegate to a new private method `_paginate_list(self, qp)`.
  2. Inside `_paginate_list`:
     - Call `PaginationParams.from_query_string(qp)` — on `ValueError` /
       `ValidationError`, return `self.error(..., status=400)`.
     - Separate pagination / sort / search keys from the remaining filter
       kwargs (these pass into `build_where_clause`'s `extra_filters`).
     - Acquire a connection: `async with await db.acquire() as conn:`.
     - Build SQL via `build_where_clause`, `build_order_by`,
       `build_count_sql`, `build_page_sql`.
     - Run `total = await conn.fetchval(count_sql)`; if `total == 0`, return
       `self.no_content(headers={"X-Total-Count": "0", ...})`.
     - Run `rows = await conn.fetch(page_sql)` and serialise each row as a dict
       (via `dict(row)`).
     - Compute `total_pages = ceil(total / page_size)` (use `-(-total //
       page_size)` or `math.ceil`).
     - Return `self.json_response(PaginatedResponse(data=..., meta=...).model_dump(),
       headers={"X-Total-Count": str(total), "X-Page": str(page),
       "X-Page-Size": str(page_size), "X-Total-Pages": str(total_pages)})`.
- Preserve **every existing path** in `get()`:
  - `meta == ':meta'` → schema JSON (untouched).
  - `query_slug` set → single-row fetch (`QueryModel.get(...)`, plus `:insert`
    branch) (untouched).
  - `len(qp) > 0 and no slug` currently calls `QueryModel.filter(**qp)` —
    **REPLACE** this branch with the new `_paginate_list(qp)` call.
  - `else` branch (`QueryModel.all(**args)`) — **REPLACE** with
    `_paginate_list({})`.
- Replace `print(...)` debug lines inside `get()` (line 110 `'INSERT > '` and
  line 118 `'ARGS '`) with `self.logger.debug(...)`. Do not remove them.
- Add a short module-level docstring update if touched.

**NOT in scope**:
- Changes to `patch`, `put`, `post`, `delete`.
- Changes to `post_init`, `get_model`, `get_query_insert`.
- Changes to `_pagination.py` (owned by TASK-624).
- Tests — see TASK-626.
- Documentation / CHANGES.md entry — see TASK-626.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/manager.py` | MODIFY | Extend `QueryManager.get()`; add `_paginate_list()` private method; replace prints with logger calls |

---

## Codebase Contract (Anti-Hallucination)

> VERIFIED against `fix-rust-deploy` HEAD on 2026-04-22.

### Verified Imports

Add to `querysource/handlers/manager.py`:

```python
from math import ceil
from pydantic import ValidationError as PydanticValidationError
from ._pagination import (
    PaginationParams,
    PaginatedResponse,
    build_where_clause,
    build_order_by,
    build_count_sql,
    build_page_sql,
)
```

Note: `datamodel.exceptions.ValidationError` is already imported at line 9 —
keep it. The Pydantic `ValidationError` is a **different** exception; import it
under an alias to avoid collision.

### Existing Signatures to Use

```python
# querysource/handlers/manager.py:18
class QueryManager(QueryView):
    _model: QueryModel = None                                      # line 19
    async def get(self): ...                                       # line 57  <-- EXTEND
    async def patch(self): ...                                     # line 137 (unchanged)
    async def delete(self): ...                                    # line 208 (unchanged)
    async def put(self): ...                                       # line 283 (unchanged)
    async def post(self): ...                                      # line 348 (unchanged)

# Already called inside get() — reuse them:
#   self.get_arguments()                               (from BaseView)
#   self.query_parameters(self.request)                (querysource/utils/handlers.py:63)
#   self.match_parameters(self.request)                (from BaseView)
#   self.json_response(body, status=..., headers=...)  (from BaseView)
#   self.no_content(headers=...)                       (from BaseView)
#   self.error(response=..., exception=..., status=..., headers=...)  (from BaseView)

# Connection acquisition pattern (verified at manager.py:102-104):
db = self.request.app['qs_connection']
async with await db.acquire() as conn:
    # conn supports: await conn.fetch(sql), conn.fetchval(sql), conn.fetchrow(sql)
    ...
```

```python
# querysource/models.py:48
class QueryModel(Model):
    class Meta:
        name = QS_QUERIES_TABLE     # string, from querysource.conf
        schema = QS_QUERIES_SCHEMA  # string, from querysource.conf
```

```python
# querysource/handlers/_pagination.py  (from TASK-624)
class PaginationParams(BaseModel):
    page: int; page_size: int
    sort_field: str; sort_direction: Literal["asc","desc"]
    search: Optional[str]; fields: Optional[list[str]]
    @property
    def offset(self) -> int: ...
    @classmethod
    def from_query_string(cls, qs: dict) -> "PaginationParams": ...

class PaginatedResponse(BaseModel):
    data: list[dict]
    meta: PaginationMeta

def build_where_clause(params, extra_filters) -> str: ...
def build_order_by(params) -> str: ...
def build_count_sql(schema, table, where) -> str: ...
def build_page_sql(schema, table, fields, where, order_by, limit, offset) -> str: ...
```

### Does NOT Exist

- ~~`QueryModel.paginate()`~~ / ~~`QueryModel.all(limit=..., offset=...)`~~ —
  confirmed not supported; use raw SQL via `conn.fetch`.
- ~~`self.paginate(...)`~~ — no such helper on `QueryView` / `BaseView`.
- ~~`QueryModel.count()`~~ — use `await conn.fetchval("SELECT COUNT(*) ...")`.
- ~~`self.request.app.qs_connection`~~ — attribute access does not work; use
  `self.request.app['qs_connection']` (dict-style, verified at line 102).
- ~~`conn.fetch_all(...)`~~ — the asyncdb pg connection exposes `.fetch(sql)`,
  not `fetch_all`.

---

## Implementation Notes

### Current relevant block to REPLACE

`querysource/handlers/manager.py:116-123`:

```python
elif len(qp) > 0:
    args = {**args, **qp}
    print('ARGS ', args)
    query = await QueryModel.filter(**qp)
else:
    query = await QueryModel.all(**args)
    query = [row.to_dict() for row in query]
return self.json_response(query)
```

Replace with:

```python
else:
    # List-pagination branch: no slug, no :meta, no :insert
    return await self._paginate_list(qp, args)
```

(The `elif len(qp) > 0` branch collapses into the same `_paginate_list` call —
filtering is part of paginated list behaviour now.)

### `_paginate_list` skeleton

```python
async def _paginate_list(self, qp: dict, default_args: dict):
    """Paginated list fetch for GET /api/v1/management/queries.

    Args:
        qp: parsed query-string parameters.
        default_args: pre-built default fields kwargs (already computed in get()).
    """
    try:
        params = PaginationParams.from_query_string(qp)
    except (ValueError, PydanticValidationError) as err:
        return self.error(
            response={"message": f"Invalid pagination params: {err}"},
            status=400,
        )

    # Remove pagination keys from qp before treating remainder as filters
    reserved = {"page", "page_size", "sort", "search", "fields"}
    extra_filters = {k: v for k, v in qp.items() if k not in reserved}

    # Resolve fields list (validated allowlist via PaginationParams)
    fields = params.fields or default_args.get("fields") or list(
        QueryModel.columns(QueryModel).keys()
    )

    schema = QueryModel.Meta.schema
    table = QueryModel.Meta.name

    try:
        where = build_where_clause(params, extra_filters)
        order_by = build_order_by(params)
        count_sql = build_count_sql(schema, table, where)
        page_sql = build_page_sql(
            schema, table, fields, where, order_by,
            limit=params.page_size, offset=params.offset,
        )
    except ValueError as err:
        return self.error(
            response={"message": f"Invalid filter/sort: {err}"},
            status=400,
        )

    db = self.request.app['qs_connection']
    try:
        async with await db.acquire() as conn:
            total = await conn.fetchval(count_sql) or 0
            headers = {
                "X-Total-Count": str(total),
                "X-Page": str(params.page),
                "X-Page-Size": str(params.page_size),
                "X-Total-Pages": str(ceil(total / params.page_size)) if total else "0",
            }
            if total == 0:
                return self.no_content(headers=headers)
            rows = await conn.fetch(page_sql)
            data = [dict(row) for row in rows]
            response = PaginatedResponse(
                data=data,
                meta={
                    "page": params.page,
                    "page_size": params.page_size,
                    "total": total,
                    "total_pages": ceil(total / params.page_size),
                },
            ).model_dump()
            return self.json_response(response, headers=headers)
    except Exception as err:
        return self.error(
            reason=f"Error paginating Query Slug list: {err}",
            exception=err,
        )
```

### Key Constraints

- **Do not break single-slug / :meta / :insert branches.** Only the two
  "everything else" branches (`elif len(qp) > 0` and `else`) are replaced.
- **No new `print()` statements.** Use `self.logger.debug` for retained
  diagnostics (the `_logger_name` is set in `post_init` at line 22).
- **No changes to other verbs.** `patch`, `put`, `post`, `delete` are out of
  scope.
- **Preserve `self.error()` / `self.no_content()` / `self.json_response()`
  usage** — keep the existing error envelope patterns.
- Async throughout, strict type hints, Google-style docstrings.

---

## Acceptance Criteria

- [ ] `GET /api/v1/management/queries` returns at most 50 rows by default, wrapped
      in the `{data, meta}` envelope defined in the spec.
- [ ] Pagination headers `X-Total-Count`, `X-Page`, `X-Page-Size`,
      `X-Total-Pages` are set.
- [ ] `?page=2&page_size=10` returns rows 11–20 of the ordered set.
- [ ] `?sort=query_slug:asc` / `?sort=updated_at:desc` both honoured.
- [ ] `?search=<term>` applies `ILIKE '%term%'` across
      `query_slug`, `description`, `program_slug`.
- [ ] `?page_size=9999` returns 400.
- [ ] Unknown sort column / unknown filter column → 400 with a useful message.
- [ ] `GET /api/v1/management/queries/{slug}` behaviour unchanged.
- [ ] `GET /api/v1/management/queries:meta` behaviour unchanged.
- [ ] `GET /api/v1/management/queries/{slug}:insert` behaviour unchanged.
- [ ] `PUT` / `POST` / `PATCH` / `DELETE` behaviour unchanged.
- [ ] No new `print()` added to `manager.py`; existing `print()` inside `get()`
      replaced with `self.logger.debug`.
- [ ] `ruff check querysource/handlers/manager.py` clean.

---

## Test Specification

> All behavioural tests live in TASK-626. This task does not author tests but
> MUST not break existing tests.

Smoke check before closing the task:
```bash
source .venv/bin/activate
pytest tests/ -v -k "queries or manager or slug" --no-header -q || true
```

---

## Agent Instructions

1. **Verify TASK-624 is complete** — `_pagination.py` must exist and import
   cleanly: `python -c "from querysource.handlers._pagination import PaginationParams"`.
2. **Re-read** `querysource/handlers/manager.py` before editing — in particular
   lines 57–135 (the full `get()` method).
3. **Update index** → `"in-progress"`.
4. **Implement** per the skeleton above, preserving all untouched branches
   byte-for-byte.
5. **Manual smoke test** with a running server if available:
   ```
   curl -i 'http://localhost:5000/api/v1/management/queries?page=1&page_size=5'
   curl -i 'http://localhost:5000/api/v1/management/queries?page_size=9999'   # expect 400
   curl -i 'http://localhost:5000/api/v1/management/queries/some_known_slug'  # expect 200 unchanged
   curl -i 'http://localhost:5000/api/v1/management/queries:meta'             # expect 200 unchanged
   ```
6. **Move** this file to `sdd/tasks/completed/`.
7. **Update index** → `"done"` and fill in the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (Claude)
**Date**: 2026-04-22
**Notes**:
- Added `_paginate_list(self, qp, default_args)` to `QueryManager` per the
  skeleton in the task. It acquires its own `qs_connection`, runs the
  COUNT + page SQL produced by `build_*_sql()`, and returns a
  `PaginatedResponse` envelope plus `X-Total-*` headers.
- Replaced the `elif len(qp) > 0` / `else` branches in `get()` with a
  single delegation to `_paginate_list(qp, args)`. Filter kwargs now flow
  through the same paginated path (and are allowlisted by
  `build_where_clause`).
- Moved the `async with await db.acquire()` inside the `if query_slug:`
  branch so the pagination path handles its own acquire — this keeps the
  two paths cleanly separated.
- Replaced `print('INSERT > ...')` and `print('ARGS ...')` in `get()` with
  `self.logger.debug(...)` using lazy-format args. The `self.logger` is
  provided by `BaseView.__init__` via `_logger_name = 'QS.Manager'`.
- `total == 0` returns `204 No Content` with pagination headers, matching
  spec §7 "Empty-result semantics".
- Imports added: `math.ceil`, `pydantic.ValidationError as
  PydanticValidationError`, and the six helpers from `._pagination`.
- Other `print()` statements in `patch`/`put`/`post`/`delete`/`get_model`
  were left untouched per the task's "NOT in scope" list.

**Deviations from spec**: none
