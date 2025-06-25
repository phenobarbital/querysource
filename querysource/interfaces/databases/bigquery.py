from typing import Union
from collections.abc import Iterable
import pandas as pd
import time
import logging
import asyncio
from google.api_core.exceptions import GoogleAPIError 
# Default BigQuery connection parameters
from ...conf import (
    BIGQUERY_CREDENTIALS,
    BIGQUERY_PROJECT_ID
)
from .abstract import AbstractDB


class BigQuery(AbstractDB):
    """BigQuery.

    Class for writing data to a BigQuery Database.
    """
    _name: str = "BigQuery"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db_credentials: dict = {
            "credentials": BIGQUERY_CREDENTIALS,
            "project_id": BIGQUERY_PROJECT_ID
        }
        self._driver: str = 'bigquery'
        self._logger = logging.getLogger(
            f'DB.{self.__class__.__name__.lower()}'
        )
    async def _wait_for_job(self, job, logger, poll_seconds: int = 2):  # NEW
        while not job.done():
            try:
                pct = getattr(job, "progress", None)
                logger.debug(
                    "LoadJob %s progreso %s – esperando %ds",
                    job.job_id,
                    f"{pct*100:.1f}%" if pct else "n/a",
                    poll_seconds,
                )
            except Exception:
                pass
            await asyncio.sleep(poll_seconds)

        # Check errors
        if getattr(job, "errors", None):
            raise RuntimeError(f"LoadJob finish with errors: {job.errors}")
        if getattr(job, "error_result", None):
            raise RuntimeError(f"LoadJob error_result: {job.error_result}")

        logger.info("LoadJob %s succesfull", job.job_id)
        return job

    async def write(
        self,
        table: str,
        schema: str,
        data: Union[pd.DataFrame, Iterable],
        on_conflict: str = "append",
        pk: list | None = None,
        use_merge: bool = False,
    ):
        """
        Writes `data` to BigQuery.  
        If `use_merge=True` and the conditions are met, it does a MERGE
        using a temporary table and explicitly waits for the LoadJob
        to finish (no arbitrary sleeps).
        """
        if not self._connection:
            self.default_connection()

        async with await self._connection.connection() as conn:
            try:
                can_merge = (
                    use_merge
                    and isinstance(data, pd.DataFrame)
                    and on_conflict == "replace"
                    and pk
                    and len(pk) > 0
                )

                if not can_merge:
                    load_job = await self._default_write(
                        conn, table, schema, data, on_conflict
                    )
                    await self._wait_for_job(load_job, self._logger)
                    return load_job 

                # ─────────────── MERGE ───────────────
                # 1) Verify that the target table exists and has data
                check_q = f"SELECT COUNT(*) AS c FROM `{schema}.{table}`"
                self._logger.debug("Check table: %s", check_q)
                result, error = await conn.query(check_q)
                if error or not result:
                    self._logger.debug("Empty/non-existent table; fallback to single load")
                    load_job = await self._default_write(
                        conn, table, schema, data, on_conflict
                    )
                    await self._wait_for_job(load_job, self._logger)
                    return load_job

                # 2) Get column types from the main table
                schema_q = f"""
                    SELECT column_name, data_type
                    FROM {schema}.INFORMATION_SCHEMA.COLUMNS
                    WHERE table_name = '{table}'
                """
                schema_res, error = await conn.query(schema_q)
                if error:
                    raise ConnectionError(f"Error checking the schema: {error}")
                column_types = {
                    row["column_name"]: row["data_type"] for row in schema_res or []
                }

                # 3) Create cloned empty temporary table
                temp_table = f"{table}_temp_{int(time.time())}"
                create_temp_q = f"""
                    CREATE TABLE `{schema}.{temp_table}`
                    AS SELECT * FROM `{schema}.{table}` WHERE 1=0
                """
                _, create_err = await conn.query(create_temp_q)
                if create_err:
                    self._logger.error("Can't create the temp table: %s", create_err)
                    load_job = await self._default_write(
                        conn, table, schema, data, on_conflict
                    )
                    await self._wait_for_job(load_job, self._logger)
                    return load_job

                try:
                    # 4) Load data on the temp table and wait
                    load_job = await self._default_write(
                        conn, temp_table, schema, data, "append"
                    )
                    await self._wait_for_job(load_job, self._logger)

                    # 5) Build and execute the merge
                    merge_keys = " AND ".join([f"T.{k} = S.{k}" for k in pk])

                    set_clause = []
                    for col in data.columns:
                        if col in pk:
                            continue
                        col_type = column_types.get(col, "STRING")
                        if col_type == "JSON":
                            set_clause.append(f"{col} = TO_JSON_STRING(S.{col})")
                        else:
                            set_clause.append(f"{col} = S.{col}")
                    set_clause_sql = ", ".join(set_clause)

                    insert_cols = ", ".join(data.columns)
                    source_cols = ", ".join([f"S.{c}" for c in data.columns])

                    merge_q = f"""
                        MERGE `{schema}.{table}` T
                        USING `{schema}.{temp_table}` S
                        ON {merge_keys}
                        WHEN MATCHED THEN UPDATE SET {set_clause_sql}
                        WHEN NOT MATCHED THEN
                            INSERT ({insert_cols}) VALUES ({source_cols})
                    """

                    result, error = await conn.query(merge_q)
                    if error:
                        raise ConnectionError(f"Error executing MERGE: {error}")

                    self._logger.info("MERGE execute succesfull")
                    return result

                finally:
                    # Clean the temp table 
                    await conn.query(f"DROP TABLE IF EXISTS `{schema}.{temp_table}`")

            except Exception as exc:
                self._logger.error("Error en write(): %s", exc, exc_info=True)
                raise

    async def _default_write(self, conn, table, schema, data, on_conflict):
        """Default write behavior without MERGE – now returns the LoadJob."""
        use_pandas = isinstance(data, pd.DataFrame)
        load_job = await conn.write(
            data=data,
            table_id=table,
            dataset_id=schema,
            if_exists=on_conflict,
            use_pandas=use_pandas
        )
        return load_job
