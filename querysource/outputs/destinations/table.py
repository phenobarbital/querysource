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

Supported methods: ``append``, ``upsert``, ``truncate``.
"""
import asyncio
from typing import List, Optional, Union
import pandas as pd
from querysource.exceptions import DataNotFound, DriverError, OutputError
from .abstract import AbstractDestination


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

VALID_METHODS = frozenset({"append", "upsert", "truncate"})

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
    """

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
        if self._method in ("append", "truncate"):
            return "append"
        # upsert → 'upsert' signals the engine to use conflict resolution
        return "upsert"

    @property
    def foreign_key(self):
        return None

    @property
    def constraint(self):
        return None

    def get_schema(self) -> str:
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
                # Clean NA strings
                u = df.select_dtypes(include=["object", "string"])
                df[u.columns] = u.replace(["<NA>", "None"], None)
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
