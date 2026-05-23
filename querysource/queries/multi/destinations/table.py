"""
Table Destination.

Writes a pandas DataFrame to a relational or external database table with
configurable write modes: ``append``, ``upsert``, and ``truncate``.

This destination reuses the existing AbstractOutput engine backends
(PgOutput, MysqlOutput, BigQueryOutput) that live in
``querysource.outputs.tables.TableOutput``.

YAML configuration example::

    Output:
      - Table:
          driver: pg
          schema: troc
          table: stores
          method: append
          pk:
            - store_id

Supported drivers: ``pg``, ``postgresql``, ``postgres``, ``mysql``,
``mariadb``, ``bigquery``, ``bq``.

Supported methods: ``append``, ``upsert``, ``truncate``, ``drop``.

Set ``create: true`` to auto-create the target table from the DataFrame
schema when it does not exist. ``method: drop`` always drops and
re-creates the table before writing.
"""
import asyncio
from typing import List, Optional, Union
import pandas as pd
from querysource.exceptions import DataNotFound, DriverError, OutputError
from querysource.outputs.destinations.abstract import AbstractDestination


# ---------------------------------------------------------------------------
# Driver normalisation
# ---------------------------------------------------------------------------

DRIVER_MAP: dict[str, str] = {
    "pg": "postgresql",
    "postgresql": "postgresql",
    "postgres": "postgresql",
    "mysql": "mysql",
    "mariadb": "mysql",
    "bigquery": "bigquery",
    "bq": "bigquery",
}

VALID_METHODS = frozenset({"append", "upsert", "truncate", "drop"})

# External (non-SQLAlchemy) engines — use db_upsert directly
_EXTERNAL_DRIVERS = frozenset({"bigquery"})


class TableDestination(AbstractDestination):
    """
    Write a DataFrame to a database table.

    Uses the existing engine classes from ``TableOutput`` to avoid
    duplicating database write logic.  The ``method`` parameter controls
    the write behaviour:

    * ``append`` — insert rows (default).
    * ``upsert`` — INSERT … ON CONFLICT UPDATE (engine-dependent).
    * ``truncate`` — TRUNCATE the table first, then append.
    * ``drop`` — DROP the table, re-create it from the DataFrame schema,
      then append.

    Set ``create=True`` to auto-create the target table from the
    DataFrame schema when it does not already exist.
    """

    # User-facing catalog override. The introspector can't recover per-attr
    # types (annotations sit on local/private vars) or the ``method`` /
    # ``driver`` enum constraints (defined as module-level frozensets), so
    # we hand-craft the catalog entry the UI consumes.
    _catalog = {
        "display_name": "Table",
        "description": (
            "Write a DataFrame to a relational or external database table "
            "with configurable write mode (append, upsert, truncate)."
        ),
        "usage": (
            "Use as a Destination step to persist a pipeline result into "
            "PostgreSQL, MySQL/MariaDB, or BigQuery. Pick the backend with "
            "``driver``, address the target with ``schema`` + ``table``, and "
            "choose the write semantics with ``method``: ``append`` inserts "
            "rows, ``upsert`` performs INSERT … ON CONFLICT UPDATE (requires "
            "``pk``), ``truncate`` empties the table before appending, and "
            "``drop`` re-creates the table from the DataFrame schema. Set "
            "``create: true`` to auto-create the table when it does not "
            "exist; when ``pk`` is configured, the PRIMARY KEY is added (or "
            "re-checked on re-runs)."
        ),
        "icon": "database",
        "attributes": [
            {
                "name": "driver",
                "type": "str",
                "required": False,
                "default": "pg",
                "description": (
                    "Backend driver. One of: ``pg`` / ``postgresql`` / "
                    "``postgres``, ``mysql`` / ``mariadb``, ``bigquery`` / "
                    "``bq``."
                ),
            },
            {
                "name": "method",
                "type": "str",
                "required": False,
                "default": "append",
                "description": (
                    "Write mode: ``append`` (insert rows), ``upsert`` "
                    "(INSERT … ON CONFLICT UPDATE — requires ``pk``), "
                    "``truncate`` (TRUNCATE table first, then append), or "
                    "``drop`` (DROP table, re-create from DataFrame schema, "
                    "then append)."
                ),
            },
            {
                "name": "create",
                "type": "bool",
                "required": False,
                "default": False,
                "description": (
                    "When True, auto-create the target table from the "
                    "DataFrame schema if it does not already exist. Ignored "
                    "when ``method=drop`` (drop always re-creates)."
                ),
            },
            {
                "name": "table",
                "type": "str",
                "required": True,
                "default": "",
                "description": "Target table name.",
            },
            {
                "name": "schema",
                "type": "str",
                "required": False,
                "default": "public",
                "description": "Database schema (relational backends).",
            },
            {
                "name": "pk",
                "type": "list",
                "required": False,
                "default": [],
                "description": (
                    "Primary-key column names. Required when "
                    "``method=upsert``."
                ),
            },
            {
                "name": "dsn",
                "type": "str",
                "required": False,
                "default": None,
                "description": (
                    "Optional explicit DSN/connection string. When omitted, "
                    "the engine's default connection is used."
                ),
            },
        ],
        "json_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "title": "Table",
            "description": "Write a DataFrame to a database table.",
            "properties": {
                "driver": {
                    "type": "string",
                    "enum": [
                        "pg", "postgresql", "postgres",
                        "mysql", "mariadb",
                        "bigquery", "bq",
                    ],
                    "default": "pg",
                },
                "method": {
                    "type": "string",
                    "enum": ["append", "upsert", "truncate", "drop"],
                    "default": "append",
                },
                "table": {"type": "string"},
                "schema": {"type": "string", "default": "public"},
                "pk": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
                "create": {"type": "boolean", "default": False},
                "dsn": {"type": ["string", "null"], "default": None},
            },
            "required": ["table"],
            "additionalProperties": False,
        },
        "example": (
            '{\n'
            '  "Output": [\n'
            '    {\n'
            '      "Table": {\n'
            '        "driver": "pg",\n'
            '        "schema": "troc",\n'
            '        "table": "stores",\n'
            '        "method": "upsert",\n'
            '        "pk": ["store_id"]\n'
            '      }\n'
            '    }\n'
            '  ]\n'
            '}'
        ),
    }

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        super().__init__(data, **kwargs)

        driver: str = kwargs.get("driver", "pg") or "pg"
        normalized = DRIVER_MAP.get(driver.lower())
        if normalized is None:
            supported = ", ".join(sorted(DRIVER_MAP))
            raise OutputError(
                f"TableDestination: unsupported driver '{driver}'. "
                f"Supported: {supported}"
            )
        self._normalized_driver: str = normalized

        method: str = (kwargs.get("method", "append") or "append").lower()
        if method not in VALID_METHODS:
            raise OutputError(
                f"TableDestination: unsupported method '{method}'. "
                f"Supported: {', '.join(sorted(VALID_METHODS))}"
            )
        self._method: str = method
        self._table: str = kwargs.get("table", "")
        self._schema: str = kwargs.get("schema", "public")
        self._pk: List[str] = kwargs.get("pk", []) or []
        self._dsn: Optional[str] = kwargs.get("dsn")
        self._create: bool = bool(kwargs.get("create", False))

    # ------------------------------------------------------------------
    # Engine factory
    # ------------------------------------------------------------------

    def _build_engine(self):
        """
        Instantiate and return the appropriate :class:`AbstractOutput` engine.

        The engine receives ``self`` as its *parent* so it can access
        ``tablename``, ``schema``, ``pk``, ``if_exists``, and other
        attributes via the same protocol as :class:`TableOutput`.

        :raises OutputError: If the engine cannot be initialised.
        """
        try:
            if self._normalized_driver == "postgresql":
                from querysource.outputs.tables.TableOutput.postgres import PgOutput
                return PgOutput(parent=self)
            elif self._normalized_driver == "mysql":
                from querysource.outputs.tables.TableOutput.mysql import MysqlOutput
                return MysqlOutput(parent=self)
            elif self._normalized_driver == "bigquery":
                from querysource.outputs.tables.TableOutput.bigquery import BigQueryOutput
                return BigQueryOutput(parent=self, external=True)
            else:
                raise OutputError(
                    f"TableDestination: no engine for driver '{self._normalized_driver}'"
                )
        except OutputError:
            raise
        except Exception as err:
            raise OutputError(
                f"TableDestination: failed to create engine: {err}"
            ) from err

    # ------------------------------------------------------------------
    # Parent-protocol attributes (read by AbstractOutput engines)
    # ------------------------------------------------------------------

    # These attributes mirror what TableOutput exposes so the existing
    # engine classes (PgOutput, BigQueryOutput, etc.) work unchanged.

    @property
    def tablename(self) -> str:
        return self._table

    @property
    def pk(self) -> List[str]:
        return self._pk

    @property
    def if_exists(self) -> str:
        """Map `method` to pandas/engine on-conflict string."""
        if self._method in ("append", "truncate", "drop"):
            return "append"
        # upsert → 'upsert' signals the engine to use conflict resolution
        return "upsert"

    @property
    def foreign_key(self):
        return None

    @property
    def constraint(self):
        return None

    def sql_schema(self) -> str:
        """Return the SQL schema name (used by engine parent-protocol).

        Note: this method is separate from :meth:`get_schema` (the classmethod
        provided by :class:`SchemaIntrospectable`) to avoid name conflicts.
        The engine parent-protocol uses this method to retrieve the schema string.
        """
        return self._schema

    def primary_keys(self) -> list:
        return self._pk

    def constraints(self):
        return None

    def foreign_keys(self):
        return None

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    async def _table_exists(self, engine) -> bool:
        """
        Return True when ``schema.table`` exists on the backend.

        For external engines (BigQuery, etc.) we cannot cheaply introspect
        without a driver-specific call, so return ``False`` and let the
        engine create the table on insert.
        """
        if engine.is_external:
            return False
        from sqlalchemy import text as sa_text

        def _check() -> bool:
            with engine.engine().connect() as conn:
                if self._normalized_driver == "postgresql":
                    sql = (
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = :s AND table_name = :t"
                    )
                else:  # mysql / mariadb
                    sql = (
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = :s AND table_name = :t"
                    )
                result = conn.execute(
                    sa_text(sql), {"s": self._schema, "t": self._table}
                )
                return result.first() is not None

        try:
            return await asyncio.to_thread(_check)
        except Exception as err:
            self.logger.warning(
                "TableDestination: existence check for %s.%s failed: %s",
                self._schema, self._table, err,
            )
            return False

    async def _drop_table(self, engine) -> None:
        """
        Execute ``DROP TABLE IF EXISTS schema.table`` on the backend.

        :param engine: An initialised engine instance.
        :raises OutputError: If the drop fails.
        """
        qualified = f"{self._schema}.{self._table}"
        try:
            if engine.is_external:
                if self._normalized_driver == "bigquery":
                    conn = engine._connection if hasattr(engine, "_connection") else None
                    if conn is None:
                        engine.connect()
                        conn = engine._connection
                    await conn.execute(
                        f"DROP TABLE IF EXISTS `{self._schema}.{self._table}`"
                    )
                    self.logger.info(
                        "TableDestination (bigquery): dropped %s", qualified
                    )
                    return
                raise OutputError(
                    f"TableDestination: DROP not supported for driver "
                    f"'{self._normalized_driver}'"
                )

            from sqlalchemy import text as sa_text

            def _do_drop() -> None:
                with engine.engine().begin() as conn:
                    conn.execute(sa_text(f"DROP TABLE IF EXISTS {qualified}"))

            await asyncio.to_thread(_do_drop)
            self.logger.info("TableDestination: dropped %s", qualified)
        except OutputError:
            raise
        except Exception as err:
            raise OutputError(
                f"TableDestination: DROP {qualified} failed: {err}"
            ) from err

    async def _create_table_from_df(
        self, df: pd.DataFrame, engine
    ) -> None:
        """
        Create ``schema.table`` from *df*'s schema (no rows).

        Uses ``DataFrame.head(0).to_sql(if_exists="fail")`` so SQLAlchemy
        emits ``CREATE TABLE`` derived from the DataFrame dtypes. The
        PRIMARY KEY is added separately by :meth:`_ensure_primary_key` so
        the same logic runs even when the table already exists (e.g. a
        previous run created it without the PK).

        For external engines (BigQuery, etc.) this is a no-op — those
        engines create the table lazily on insert.

        :param df: DataFrame whose schema drives the CREATE.
        :param engine: An initialised engine instance.
        :raises OutputError: If the CREATE fails.
        """
        qualified = f"{self._schema}.{self._table}"
        if engine.is_external:
            self.logger.info(
                "TableDestination: create=True is a no-op for external "
                "driver '%s'; engine will create the table on insert.",
                self._normalized_driver,
            )
            return

        try:
            await asyncio.to_thread(
                df.head(0).to_sql,
                name=self._table,
                con=engine.engine(),
                schema=self._schema,
                index=False,
                if_exists="fail",
            )
            self.logger.info(
                "TableDestination: created %s from DataFrame schema", qualified
            )
        except Exception as err:
            raise OutputError(
                f"TableDestination: CREATE {qualified} failed: {err}"
            ) from err

    async def _has_primary_key(self, engine) -> bool:
        """Return True if the target table has any PRIMARY KEY constraint.

        Used by :meth:`_ensure_primary_key` to keep the ALTER idempotent.
        External engines manage PKs themselves — treat as "yes" so we don't
        attempt an unsupported ALTER on them.
        """
        if engine.is_external:
            return True
        from sqlalchemy import text as sa_text

        def _check() -> bool:
            with engine.engine().connect() as conn:
                result = conn.execute(
                    sa_text(
                        "SELECT 1 FROM information_schema.table_constraints "
                        "WHERE table_schema = :s AND table_name = :t "
                        "AND constraint_type = 'PRIMARY KEY' LIMIT 1"
                    ),
                    {"s": self._schema, "t": self._table},
                )
                return result.first() is not None

        try:
            return await asyncio.to_thread(_check)
        except Exception as err:
            self.logger.warning(
                "TableDestination: PK existence check for %s.%s failed: %s — "
                "assuming PK exists to skip duplicate ALTER.",
                self._schema, self._table, err,
            )
            return True

    async def _ensure_primary_key(self, engine) -> None:
        """Idempotently ensure ``self._pk`` is the table's PRIMARY KEY.

        Runs whenever ``create=True`` and a ``pk`` is configured. Safe to
        call repeatedly: a pre-existing PRIMARY KEY (regardless of column
        list) short-circuits the ALTER. Failures are raised — never
        swallowed — so users see the real error instead of a silent skip
        followed by ``ON CONFLICT`` blowing up at INSERT time.

        For external engines (BigQuery, etc.) this is a no-op.
        """
        if not self._pk or engine.is_external:
            return
        if await self._has_primary_key(engine):
            return

        qualified = f"{self._schema}.{self._table}"
        pk_cols = ", ".join(f'"{c}"' for c in self._pk)
        from sqlalchemy import text as sa_text

        def _add_pk() -> None:
            with engine.engine().begin() as conn:
                conn.execute(
                    sa_text(
                        f"ALTER TABLE {qualified} ADD PRIMARY KEY ({pk_cols})"
                    )
                )

        try:
            await asyncio.to_thread(_add_pk)
            self.logger.info(
                "TableDestination: added PRIMARY KEY (%s) on %s",
                ", ".join(self._pk), qualified,
            )
        except Exception as err:
            raise OutputError(
                f"TableDestination: failed to add PRIMARY KEY ({pk_cols}) "
                f"on {qualified}: {err}"
            ) from err

    async def _truncate_table(self, engine) -> None:
        """
        Execute ``TRUNCATE TABLE schema.table`` via the engine connection.

        For non-external (SQLAlchemy) engines: execute raw SQL via the
        engine's async connection.

        For external engines (BigQuery, DocumentDB, etc.): fall back to a
        driver-specific truncate approach — currently only BigQuery is
        supported here (``TRUNCATE TABLE`` DDL).

        :param engine: An initialised engine instance.
        :raises OutputError: If truncation fails.
        """
        qualified = f"{self._schema}.{self._table}"
        try:
            if not engine.is_external:
                # SQLAlchemy-based engine — run raw SQL
                from sqlalchemy import text as sa_text
                async with engine.engine().begin() as conn:
                    await conn.execute(
                        sa_text(f"TRUNCATE TABLE {qualified}")
                    )
                    self.logger.info(
                        "TableDestination: truncated %s", qualified
                    )
            else:
                # External engine — attempt driver-specific truncate
                if self._normalized_driver == "bigquery":
                    conn = engine._connection if hasattr(engine, "_connection") else None
                    if conn is None:
                        engine.connect()
                        conn = engine._connection
                    sql = f"TRUNCATE TABLE `{self._schema}.{self._table}`"
                    await conn.execute(sql)
                    self.logger.info(
                        "TableDestination (bigquery): truncated %s", qualified
                    )
                else:
                    self.logger.warning(
                        "TableDestination: truncate not supported for driver "
                        "'%s' — performing replace instead.",
                        self._normalized_driver,
                    )
        except Exception as err:
            raise OutputError(
                f"TableDestination: TRUNCATE {qualified} failed: {err}"
            ) from err

    async def _ensure_table(self, df: pd.DataFrame, engine) -> None:
        """
        Ensure the target table exists with the declared PRIMARY KEY before
        writing.

        * When ``method=drop`` the table is always re-created from *df*.
        * When ``create=True`` the table is created only if it does not
          already exist.

        In both branches, when a ``pk`` is configured we additionally call
        :meth:`_ensure_primary_key`. This is essential when the table was
        created on a previous run (or manually) without the declared PK —
        otherwise the next ``INSERT ... ON CONFLICT`` (used by the engine's
        upsert path) would fail with *"no unique or exclusion constraint
        matching the ON CONFLICT specification"*.
        """
        if self._method == "drop":
            # Drop unconditionally (handles missing table), then create.
            await self._drop_table(engine)
            await self._create_table_from_df(df, engine)
            await self._ensure_primary_key(engine)
            return

        if self._create:
            if not await self._table_exists(engine):
                await self._create_table_from_df(df, engine)
            await self._ensure_primary_key(engine)

    async def _write_to_table(self, df: pd.DataFrame, engine) -> None:
        """
        Write *df* to the configured table using *engine*.

        Handles both SQLAlchemy-based and external engines.

        :param df: DataFrame to write.
        :param engine: Initialised engine instance.
        :raises OutputError: On write failure.
        """
        try:
            if not engine.is_external:
                # --- SQLAlchemy path: pandas to_sql ---
                options: dict = {
                    "chunksize": 1000,
                    "schema": self._schema,
                    "index": False,
                    "if_exists": "append",  # always append; truncate handled separately
                    "method": engine.db_upsert,
                }
                if self._pk:
                    options["index_label"] = self._pk
                engine.columns = list(df.columns)
                self._columns = engine.columns
                # Clean NA-string sentinels. Use a local copy + .loc so we
                # don't mutate a slice that the caller still holds (which
                # triggered SettingWithCopyWarning under chained-assignment
                # semantics).
                obj_cols = df.select_dtypes(include=["object", "string"]).columns
                if len(obj_cols):
                    df = df.copy()
                    df.loc[:, obj_cols] = df.loc[:, obj_cols].replace(
                        ["<NA>", "None"], None
                    )
                await asyncio.to_thread(
                    df.to_sql,
                    name=self._table,
                    con=engine.engine(),
                    **options,
                )
                self.logger.info(
                    "TableDestination: wrote %d rows → %s.%s",
                    len(df),
                    self._schema,
                    self._table,
                )
            else:
                # --- External engine path: db_upsert ---
                on_conflict = "replace" if self._method == "append" else "upsert"
                await engine.db_upsert(
                    data=df,
                    table=self._table,
                    schema=self._schema,
                    on_conflict=on_conflict,
                    pk=self._pk or None,
                )
                self.logger.info(
                    "TableDestination: wrote %d rows → %s.%s",
                    len(df),
                    self._schema,
                    self._table,
                )
        except Exception as err:
            raise OutputError(
                f"TableDestination: write to {self._schema}.{self._table} failed: {err}"
            ) from err

    # ------------------------------------------------------------------
    # AbstractDestination interface
    # ------------------------------------------------------------------

    async def run(self) -> Union[dict, pd.DataFrame]:
        """
        Write :attr:`data` to the configured table.

        Handles both a single :class:`~pandas.DataFrame` and a ``dict``
        of DataFrames.  Each DataFrame is written to the same table
        (successive writes).

        :returns: Original :attr:`data` (pass-through).
        :raises OutputError: On write failure.
        :raises DataNotFound: If :attr:`data` is empty.
        """
        engine = self._build_engine()
        try:
            frames: list[pd.DataFrame]
            if isinstance(self.data, dict):
                frames = [
                    df for df in self.data.values()
                    if isinstance(df, pd.DataFrame) and not df.empty
                ]
                if not frames:
                    raise DataNotFound("TableDestination: all DataFrames in dict are empty.")
            elif isinstance(self.data, pd.DataFrame):
                if self.data.empty:
                    raise DataNotFound("TableDestination: DataFrame is empty.")
                frames = [self.data]
            else:
                raise DriverError(
                    f"TableDestination: expected DataFrame or dict, got {type(self.data)}"
                )

            # Pre-write DDL: drop+create or create-if-missing.
            # Uses the first frame's schema to derive the CREATE.
            if self._method == "drop" or self._create:
                await self._ensure_table(frames[0], engine)

            # Truncate once before writing all frames
            if self._method == "truncate":
                await self._truncate_table(engine)

            for df in frames:
                await self._write_to_table(df, engine)

        finally:
            try:
                if asyncio.iscoroutinefunction(engine.close):
                    await engine.close()
                else:
                    engine.close()
            except Exception as close_err:
                self.logger.warning(
                    "TableDestination: engine close error: %s", close_err
                )

        return self.data
