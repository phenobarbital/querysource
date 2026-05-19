"""SourceTable — query a database table and return results as a pandas DataFrame.

Connects to a database via ``asyncdb.AsyncDB``, executes
``SELECT * FROM schema.table [WHERE ...]``, and returns the result as a
pandas DataFrame.

Dependency: ``asyncdb`` (already a project dependency).
"""
import re

import pandas as pd
from aiohttp import web

from .base import ThreadSource

import asyncio

# Driver alias normalisation map (matches ai-parrot TableSource pattern)
DRIVER_ALIASES: dict[str, str] = {
    "postgresql": "pg",
    "postgres": "pg",
    "bq": "bigquery",
    "mariadb": "mysql",
}

# SQL identifier validation — prevents injection via table/schema/column names
SQL_IDENTIFIER_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


class SourceTable(ThreadSource):
    """Query a database table and return the result as a pandas DataFrame.

    Executes ``SELECT * FROM [schema.]table [WHERE ...]`` using an asyncdb
    connection.  The driver can be specified using short aliases (e.g. ``pg``
    for PostgreSQL, ``bigquery`` for Google BigQuery).

    Configuration dict shape::

        {
            "driver": "pg",
            "schema": "troc",
            "table": "stores",
            "filter": {"active": true},
            "credentials": {         # optional
                "host": "localhost",
                "port": 5432,
                "user": "myuser",
                "password": "mypassword",
                "database": "mydb"
            }
        }

    Or using a DSN string::

        {
            "driver": "pg",
            "schema": "public",
            "table": "users",
            "dsn": "postgresql://user:pwd@host:5432/db"
        }
    """

    def __init__(
        self,
        name: str,
        options: dict,
        request: web.Request,
        queue: asyncio.Queue,
    ):
        super().__init__(name, options, request, queue)
        driver = options.get('driver', 'pg')
        self._driver: str = DRIVER_ALIASES.get(driver, driver)
        self._schema: str | None = options.get('schema')
        self._table: str | None = options.get('table')
        self._filter: dict = options.get('filter', {})
        self._dsn: str | None = options.get('dsn')
        self._credentials: dict = options.get('credentials', {})

        # Validate SQL identifiers early to fail fast.
        if self._table and not SQL_IDENTIFIER_RE.match(self._table):
            raise ValueError(f"Invalid table name: {self._table!r}")
        if self._schema and not SQL_IDENTIFIER_RE.match(self._schema):
            raise ValueError(f"Invalid schema name: {self._schema!r}")

    def _build_where(self) -> str:
        """Build a WHERE clause from the filter dict with proper escaping.

        Returns:
            An empty string when no filters are present, otherwise a string
            starting with " WHERE " followed by AND-joined conditions.

        Raises:
            ValueError: If a filter column name fails the SQL identifier check.
        """
        if not self._filter:
            return ""

        clauses: list[str] = []
        for col, val in self._filter.items():
            if not SQL_IDENTIFIER_RE.match(col):
                raise ValueError(f"Invalid column name in filter: {col!r}")
            if isinstance(val, bool):
                clauses.append(f"{col} = {str(val).lower()}")
            elif isinstance(val, (int, float)):
                clauses.append(f"{col} = {val}")
            elif isinstance(val, str):
                safe_val = val.replace("'", "''")
                clauses.append(f"{col} = '{safe_val}'")
            elif val is None:
                clauses.append(f"{col} IS NULL")
            else:
                # Fallback: cast to string and quote
                safe_val = str(val).replace("'", "''")
                clauses.append(f"{col} = '{safe_val}'")

        return " WHERE " + " AND ".join(clauses)

    async def fetch(self) -> pd.DataFrame:
        """Connect to the database and execute the SELECT query.

        Returns:
            A pandas DataFrame containing the query results.

        Raises:
            ValueError: If ``table`` is not provided.
            RuntimeError: If the query returns errors.
        """
        from asyncdb import AsyncDB  # noqa: PLC0415

        if not self._table:
            raise ValueError("SourceTable: 'table' is required.")

        table_ref = (
            f"{self._schema}.{self._table}" if self._schema else self._table
        )
        sql = f"SELECT * FROM {table_ref}{self._build_where()}"

        if self._dsn:
            db = AsyncDB(self._driver, dsn=self._dsn)
        elif self._credentials:
            resolved = {
                k: self.resolve_credential(k, v)
                for k, v in self._credentials.items()
            }
            db = AsyncDB(self._driver, params=resolved)
        else:
            db = AsyncDB(self._driver)

        async with db as conn:
            conn.output_format('pandas')
            result, errors = await conn.query(sql)
            if errors:
                raise RuntimeError(f"SourceTable query error: {errors}")

        if result is None:
            return pd.DataFrame()
        return result
