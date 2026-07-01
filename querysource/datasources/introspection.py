"""Schema introspection strategies, one per driver family.

The QuerySource executor exposes a single ``/api/v1/queries/schema`` endpoint
(see ``querysource.handlers.executor.QueryExecutor.schema``). The frontend calls
it with a ``driver`` or ``datasource`` and renders a uniform tree:

    schema → table → column

Every backend speaks a different dialect (ANSI ``information_schema`` for the
relational drivers, the BigQuery client API for datasets, Flux ``schema.*`` for
InfluxDB buckets/measurements, ``SCAN`` for Redis namespaces). This module hides
those differences behind a small ``Introspector`` contract so the handler — and
the frontend — stay driver-agnostic.

Adding a new driver = register one ``Introspector`` in ``INTROSPECTORS``. Nothing
else in the request path needs to change.

Uniform output shapes (keys are intentionally lowercase and stable, the frontend
relies on them):

* ``tables()``  -> ``[{"table_schema": str, "table_name": str, "col_count": int}]``
* ``columns()`` -> ``[{"column_name": str, "data_type": str, "is_nullable": str}]``
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional


logger = logging.getLogger("querysource.introspection")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _as_dicts(rows: Any) -> list[dict]:
    """Normalize a driver's row collection to a list of plain dicts.

    asyncpg returns ``Record`` objects, pymssql/pytds return dicts, others may
    return mapping-like rows. ``dict(row)`` covers Record and Mapping; we fall
    back gracefully so a single odd row never breaks introspection.
    """
    out: list[dict] = []
    for row in rows or []:
        if isinstance(row, dict):
            out.append(row)
            continue
        if hasattr(row, "_asdict"):
            out.append(dict(row._asdict()))
            continue
        try:
            out.append(dict(row))
        except (TypeError, ValueError):
            logger.debug("introspection: could not coerce row to dict: %r", row)
    return out


def _sql_quote(value: str) -> str:
    """Escape a SQL string literal (double single quotes)."""
    return "'" + str(value).replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------
class Introspector(ABC):
    """Per-driver schema introspection contract.

    ``supported`` lets a driver advertise that introspection is unavailable
    (e.g. not yet implemented) without raising — the handler turns it into a
    clean message instead of a 500.
    """

    supported: bool = True
    #: Human-readable reason shown when ``supported`` is False.
    reason: Optional[str] = None

    @abstractmethod
    async def tables(self, conn: Any) -> list[dict]:
        """Return ``[{table_schema, table_name, col_count}]`` for the connection."""

    @abstractmethod
    async def columns(self, conn: Any, schema: str, table: str) -> list[dict]:
        """Return ``[{column_name, data_type, is_nullable}]`` for one entity."""


class Unsupported(Introspector):
    """Sentinel introspector for drivers without (yet) a strategy."""

    supported = False

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def tables(self, conn: Any) -> list[dict]:
        return []

    async def columns(self, conn: Any, schema: str, table: str) -> list[dict]:
        return []


# ---------------------------------------------------------------------------
# ANSI information_schema (PostgreSQL, MySQL/MariaDB, SQL Server)
# ---------------------------------------------------------------------------
class AnsiSQLIntrospector(Introspector):
    """Relational drivers exposing the ANSI ``information_schema.columns`` view.

    Only two things actually differ between dialects, so they are parameters:

    * ``count_expr`` — how to cast/produce an integer column count. PostgreSQL
      accepts ``COUNT(*)::int``; that ``::`` cast is invalid in T-SQL and MySQL,
      where a plain ``COUNT(*)`` is correct.
    * ``excluded_schemas`` — engine-specific system schemas to hide.
    """

    def __init__(self, count_expr: str, excluded_schemas: tuple[str, ...]) -> None:
        self.count_expr = count_expr
        self.excluded_schemas = excluded_schemas

    def _tables_sql(self) -> str:
        sql = [
            f"SELECT table_schema, table_name, {self.count_expr} AS col_count",
            "FROM information_schema.columns",
        ]
        if self.excluded_schemas:
            excluded = ", ".join(_sql_quote(s) for s in self.excluded_schemas)
            sql.append(f"WHERE table_schema NOT IN ({excluded})")
        sql += [
            "GROUP BY table_schema, table_name",
            "ORDER BY table_schema, table_name",
        ]
        return " ".join(sql)

    def _columns_sql(self, schema: str, table: str) -> str:
        return " ".join([
            "SELECT column_name, data_type, is_nullable",
            "FROM information_schema.columns",
            f"WHERE table_schema = {_sql_quote(schema)} "
            f"AND table_name = {_sql_quote(table)}",
            "ORDER BY ordinal_position",
        ])

    async def _run(self, conn: Any, sql: str) -> list[dict]:
        result, error = await conn.query(sql)
        if error:
            raise RuntimeError(str(error))
        return _as_dicts(result)

    async def tables(self, conn: Any) -> list[dict]:
        rows = await self._run(conn, self._tables_sql())
        return [
            {
                "table_schema": r.get("table_schema"),
                "table_name": r.get("table_name"),
                "col_count": int(r.get("col_count") or 0),
            }
            for r in rows
        ]

    async def columns(self, conn: Any, schema: str, table: str) -> list[dict]:
        rows = await self._run(conn, self._columns_sql(schema, table))
        return [
            {
                "column_name": r.get("column_name"),
                "data_type": r.get("data_type"),
                "is_nullable": r.get("is_nullable"),
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# BigQuery (Google client API — region-agnostic, no SQL billing)
# ---------------------------------------------------------------------------
class BigQueryIntrospector(Introspector):
    """BigQuery introspection via the ``google.cloud.bigquery`` client.

    BigQuery ``INFORMATION_SCHEMA`` is *region-scoped* (``region-us`` ≠
    ``us-central1``), so a single SQL query cannot enumerate datasets that live
    in different locations. The client REST API (``list_datasets`` /
    ``list_tables`` / ``get_table``) is global and region-agnostic — exactly how
    the BigQuery console lists everything — so we use it instead.

    Dataset = ``table_schema``; table = ``table_name``; column = a BigQuery
    field. ``col_count`` is left at 0 in ``tables()`` (the API does not return
    it cheaply) and is implicitly available once columns are lazy-loaded.
    """

    def _client(self, conn: Any) -> Any:
        # asyncdb BigQuery driver keeps the bq.Client on the connection.
        client = getattr(conn, "get_connection", lambda: None)()
        if client is None:
            client = getattr(conn, "_connection", None)
        if client is None:
            raise RuntimeError("BigQuery client connection is not available")
        return client

    async def tables(self, conn: Any) -> list[dict]:
        client = self._client(conn)
        out: list[dict] = []
        for dataset in client.list_datasets():  # global, all regions
            ds_id = dataset.dataset_id
            try:
                for tbl in client.list_tables(dataset.reference):
                    out.append({
                        "table_schema": ds_id,
                        "table_name": tbl.table_id,
                        "col_count": 0,
                    })
            except Exception as err:  # noqa: BLE001 - one dataset must not break all
                logger.warning("BigQuery list_tables failed for %s: %s", ds_id, err)
        out.sort(key=lambda r: (r["table_schema"], r["table_name"]))
        return out

    async def columns(self, conn: Any, schema: str, table: str) -> list[dict]:
        client = self._client(conn)
        ref = f"{client.project}.{schema}.{table}"
        tbl = client.get_table(ref)
        return [
            {
                "column_name": field.name,
                "data_type": field.field_type,
                "is_nullable": "NO" if field.mode == "REQUIRED" else "YES",
            }
            for field in tbl.schema
        ]


# ---------------------------------------------------------------------------
# InfluxDB 2.x (buckets / measurements / tags+fields via Flux schema.*)
# ---------------------------------------------------------------------------
class InfluxIntrospector(Introspector):
    """InfluxDB 2.x introspection.

    Maps the time-series model onto the common tree:
    bucket = ``table_schema``; measurement = ``table_name``; tag/field keys =
    columns. Buckets come from the management API (``list_buckets``);
    measurements and keys come from the ``influxdata/influxdb/schema`` Flux
    package. Best-effort: a bucket that rejects a schema query is skipped rather
    than failing the whole call.
    """

    _FLUX_MEASUREMENTS = (
        'import "influxdata/influxdb/schema"\n'
        'schema.measurements(bucket: {bucket})'
    )
    _FLUX_FIELD_KEYS = (
        'import "influxdata/influxdb/schema"\n'
        'schema.measurementFieldKeys(bucket: {bucket}, measurement: {measurement})'
    )
    _FLUX_TAG_KEYS = (
        'import "influxdata/influxdb/schema"\n'
        'schema.measurementTagKeys(bucket: {bucket}, measurement: {measurement})'
    )

    @staticmethod
    def _flux_str(value: str) -> str:
        return '"' + str(value).replace('"', '\\"') + '"'

    async def _flux_values(self, conn: Any, flux: str) -> list[str]:
        result, error = await conn.query(flux, frmt="recordset")
        if error:
            raise RuntimeError(str(error))
        values: list[str] = []
        for row in _as_dicts(result):
            # schema.* returns the value under `_value`.
            val = row.get("_value")
            if val is not None:
                values.append(str(val))
        return values

    async def tables(self, conn: Any) -> list[dict]:
        buckets = await conn.list_buckets()
        out: list[dict] = []
        for bucket in buckets:
            name = getattr(bucket, "name", None) or str(bucket)
            try:
                flux = self._FLUX_MEASUREMENTS.format(bucket=self._flux_str(name))
                for measurement in await self._flux_values(conn, flux):
                    out.append({
                        "table_schema": name,
                        "table_name": measurement,
                        "col_count": 0,
                    })
            except Exception as err:  # noqa: BLE001
                logger.warning("Influx measurements failed for bucket %s: %s", name, err)
        out.sort(key=lambda r: (r["table_schema"], r["table_name"]))
        return out

    async def columns(self, conn: Any, schema: str, table: str) -> list[dict]:
        bucket, measurement = self._flux_str(schema), self._flux_str(table)
        cols: list[dict] = []
        try:
            tag_flux = self._FLUX_TAG_KEYS.format(bucket=bucket, measurement=measurement)
            for tag in await self._flux_values(conn, tag_flux):
                if tag.startswith("_"):
                    continue
                cols.append({"column_name": tag, "data_type": "tag", "is_nullable": "YES"})
        except Exception as err:  # noqa: BLE001
            logger.warning("Influx tag keys failed for %s.%s: %s", schema, table, err)
        try:
            field_flux = self._FLUX_FIELD_KEYS.format(bucket=bucket, measurement=measurement)
            for field in await self._flux_values(conn, field_flux):
                cols.append({"column_name": field, "data_type": "field", "is_nullable": "YES"})
        except Exception as err:  # noqa: BLE001
            logger.warning("Influx field keys failed for %s.%s: %s", schema, table, err)
        return cols


# ---------------------------------------------------------------------------
# Redis (key namespaces via SCAN — best effort)
# ---------------------------------------------------------------------------
class RedisIntrospector(Introspector):
    """Redis introspection.

    Redis has no schema; we approximate one by sampling keys with ``SCAN`` and
    grouping by their first ``:``-delimited segment (the common namespacing
    convention, e.g. ``user:42`` → namespace ``user``). The single pseudo-schema
    is ``keys``; each namespace becomes a ``table`` whose ``col_count`` is the
    number of sampled keys. Columns of a namespace are a sample of concrete keys
    with their Redis ``TYPE``. Sampling is capped to keep large keyspaces safe.
    """

    _SCAN_COUNT = 500
    _MAX_KEYS = 5000
    _SAMPLE_PER_NS = 50

    async def _scan_keys(self, conn: Any) -> list[str]:
        keys: list[str] = []
        cursor = 0
        while True:
            cursor, batch = await conn.execute("SCAN", cursor, "COUNT", self._SCAN_COUNT)
            for k in batch:
                keys.append(k.decode() if isinstance(k, (bytes, bytearray)) else str(k))
            cursor = int(cursor)
            if cursor == 0 or len(keys) >= self._MAX_KEYS:
                break
        return keys

    @staticmethod
    def _namespace(key: str) -> str:
        return key.split(":", 1)[0] if ":" in key else key

    async def tables(self, conn: Any) -> list[dict]:
        keys = await self._scan_keys(conn)
        counts: dict[str, int] = {}
        for key in keys:
            ns = self._namespace(key)
            counts[ns] = counts.get(ns, 0) + 1
        out = [
            {"table_schema": "keys", "table_name": ns, "col_count": n}
            for ns, n in counts.items()
        ]
        out.sort(key=lambda r: r["table_name"])
        return out

    async def columns(self, conn: Any, schema: str, table: str) -> list[dict]:
        # `table` is the namespace prefix; list a sample of concrete keys + type.
        prefix = f"{table}:*" if table else "*"
        cursor, sampled = 0, []
        while len(sampled) < self._SAMPLE_PER_NS:
            cursor, batch = await conn.execute("SCAN", cursor, "MATCH", prefix, "COUNT", self._SCAN_COUNT)
            for k in batch:
                sampled.append(k.decode() if isinstance(k, (bytes, bytearray)) else str(k))
                if len(sampled) >= self._SAMPLE_PER_NS:
                    break
            cursor = int(cursor)
            if cursor == 0:
                break
        cols: list[dict] = []
        for key in sampled:
            try:
                ktype = await conn.execute("TYPE", key)
                ktype = ktype.decode() if isinstance(ktype, (bytes, bytearray)) else str(ktype)
            except Exception:  # noqa: BLE001
                ktype = "unknown"
            cols.append({"column_name": key, "data_type": ktype, "is_nullable": "YES"})
        return cols


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
INTROSPECTORS: dict[str, Introspector] = {
    "postgres": AnsiSQLIntrospector(
        count_expr="COUNT(*)::int",
        excluded_schemas=("pg_catalog", "information_schema", "pg_toast"),
    ),
    "mysql": AnsiSQLIntrospector(
        count_expr="COUNT(*)",
        excluded_schemas=("information_schema", "mysql", "performance_schema", "sys"),
    ),
    "mssql": AnsiSQLIntrospector(
        count_expr="COUNT(*)",
        excluded_schemas=("sys", "INFORMATION_SCHEMA"),
    ),
    "bigquery": BigQueryIntrospector(),
    "influx": InfluxIntrospector(),
    "redis": RedisIntrospector(),
}


def driver_family(driver: str) -> str:
    """Normalize a driver name to its introspection family."""
    name = (driver or "").lower().strip()
    if name in ("pg", "postgres", "postgresql"):
        return "postgres"
    if name in ("mysql", "mariadb"):
        return "mysql"
    if name in ("mssql", "sqlserver", "mstds"):
        return "mssql"
    if name in ("influx", "influxdb"):
        return "influx"
    return name  # bigquery, redis, …


def get_introspector(driver: str) -> Introspector:
    """Return the Introspector for ``driver`` (or an Unsupported sentinel)."""
    family = driver_family(driver)
    introspector = INTROSPECTORS.get(family)
    if introspector is None:
        return Unsupported(
            reason=f'Schema introspection is not supported for driver "{driver}".'
        )
    return introspector
