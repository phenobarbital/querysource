Unreleased
==========

Describe API for query slugs (FEAT-148)
---------------------------------------

New read-only endpoints for discovering stored query slugs without executing them:
``GET /api/v1/queries/describe`` (paginated list), ``GET /api/v1/queries/{slug}/describe``
(definition + typed variables), ``GET /api/v1/queries/{slug}/columns`` (prepared, never
executed) and ``GET /api/v1/queries/vocabulary`` (relative-date keywords).

- Access rules: Requires a principal (session or sessionless authz). List visibility requires ``slug:list`` or ``slug:execute``; detail and columns require ``slug:describe`` or ``slug:execute``. Denials return ``404`` to prevent enumeration.
- Redaction: Sensitive fields (like ``query_raw``) are redacted unless the principal has ``slug:describe_raw``. Admin-only fields (like ``dwh_info``, ``cache_options``) are visible only to superusers or members of ``QS_DESCRIBE_ADMIN_GROUPS``.
- Configuration: Configured via ``QS_DESCRIBE_MAX_SCAN`` (default 10000), ``QS_DESCRIBE_ADMIN_GROUPS`` (default admin,superuser), and ``QS_DESCRIBE_COLUMNS_TIMEOUT`` (default 5).
- Policy: Default policies in ``policies/defaults.yaml`` (such as ``admin_full_access``) are updated to grant ``slug:describe`` and ``slug:describe_raw``.
- Behaviour change: Relative-date keywords (``TODAY``, ``YESTERDAY``, ``FDOM``, ``LDOM``, ``CURRENT_YEAR``, ``CURRENT_MONTH``, ``LAST_YEAR``) now resolve consistently on raw-query providers for untyped and date/datetime/timestamp conditions. Environment overrides for ``UDF_LIST``, ``PG_CONSTANTS``, and ``PG_UDF`` now accept comma-separated values.

Row-oriented outputs — DataFrame results and swallowed errors
-------------------------------------------------------------

The ``iter`` output format now honours its contract and returns a list of
dictionaries for ``pandas.DataFrame`` results (``bigquery``, ``deltatbl``,
``iceberg`` providers) instead of passing the frame through. Iterating a
DataFrame yields column names, not rows, so every writer declaring
``output_format = 'iter'`` received garbage: ``aiocsv`` raised
``AttributeError`` in ``CSVWriter``/``TSVWriter`` and the writers'
``TmpFile.__aexit__`` returned a truthy value, suppressing the exception —
``slug:csv`` answered HTTP 200 with only the header line. ``slug:txt`` and the
report writers (``slug:html``, ``slug:pdf``) were broken by the same cause.

- ``iterFormat.serialize`` converts DataFrames to records
  (``NaN``/``NaT``/``NA`` -> ``None``) via the new
  ``querysource.utils.dataframes`` helpers.
- ``CSVWriter``/``TSVWriter`` apply the same normalisation defensively, for
  callers handing a frame straight to a writer.
- ``TmpFile.__aexit__`` no longer suppresses exceptions, so writer errors
  surface as HTTP 500 instead of a silent, truncated 200.
Drop invalid ``Content-Range`` header from streamed responses
------------------------------------------------------------

``AbstractWriter.stream_response()`` advertised
``Content-Range: bytes 0-16384/<content-length>`` on ``200`` responses that
carry the **full** body. The header is only defined for ``206``/``416``
(RFC 9110 s14.4), the range is off by one (inclusive positions, so
``0-16384`` spans 16385 bytes) and for any body under 16385 bytes the
last-byte-pos exceeded the complete length, making the field value invalid.
QuerySource does not honour request ``Range`` headers at all, so nothing
relied on it. ``Content-Length`` is unchanged.

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
