Unreleased
==========

FEAT-090 — Query Slug list pagination
-------------------------------------

**BREAKING**: ``GET /api/v1/management/queries`` now returns a paginated
envelope (``{"data": [...], "meta": {...}}``) instead of a bare JSON array.
The response is capped at 200 rows per request (default page size 50).

New query parameters on ``GET /api/v1/management/queries``:

- ``page`` (int, default ``1``)
- ``page_size`` (int, default ``50``, max ``200``)
- ``sort=<field>[:asc|desc]`` — allowlisted columns only
  (``query_slug``, ``description``, ``program_slug``, ``provider``,
  ``is_cached``, ``created_at``, ``updated_at``)
- ``search=<term>`` — ``ILIKE '%term%'`` across ``query_slug``,
  ``description``, ``program_slug`` and ``source``
- ``fields=<csv>`` — same allowlist as before, now validated against
  ``QueryModel.columns`` (unknown columns rejected with ``400``)

Any remaining query-string key that matches a ``QueryModel`` column is
still accepted as an equality filter; unknown / unsafe keys are dropped
rather than forwarded to SQL.

New response headers: ``X-Total-Count``, ``X-Page``, ``X-Page-Size``,
``X-Total-Pages``. Empty results return ``204 No Content`` with
``X-Total-Count: 0`` (same semantics as the previous ``NoDataFound`` path).

Unchanged:

- ``GET /api/v1/management/queries/{slug}``
- ``GET /api/v1/management/queries:meta``
- ``GET /api/v1/management/queries/{slug}:insert``
- ``PUT`` / ``POST`` / ``PATCH`` / ``DELETE`` verbs


2.8.0 (2022-09-29)
==================

- new support for models based on datamodel
- upgraded version of asyncdb
- migrated parsers to cython
- added support for stored procedures in SQL Server driver.


2.7.7 (2022-08-02)
==================

- Removing dependency of navigator.conf
- added new navigator-session dependency
- security fixes
- bump version packages.
