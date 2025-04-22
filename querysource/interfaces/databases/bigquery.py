from typing import Union
from collections.abc import Iterable
import pandas as pd
import time
import logging
import asyncio
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

    async def write(
        self,
        table: str,
        schema: str,
        data: Union[pd.DataFrame, Iterable],
        on_conflict: str = 'append',
        pk: list = None,
        use_merge: bool = False
    ):
        """Write data to BigQuery with optional MERGE support."""
        if not self._connection:
            self.default_connection()

        async with await self._connection.connection() as conn:
            try:
                can_merge = (
                    use_merge and isinstance(data, pd.DataFrame) and on_conflict == 'replace' and pk and len(pk) > 0
                )

                if not can_merge:
                    return await self._default_write(conn, table, schema, data, on_conflict)

                # Check if the table exists and has data
                check_query = f"SELECT COUNT(*) as count FROM `{schema}.{table}`"
                self._logger.debug(f"Executing table check query: {check_query}")
                result, error = await conn.query(check_query)

                if error or not result:
                    self._logger.debug(
                        "Table check failed or empty result, using default write"
                    )
                    return await self._default_write(conn, table, schema, data, on_conflict)

                # Get the schema of the original table
                schema_query = f"""
                SELECT column_name, data_type
                FROM {schema}.INFORMATION_SCHEMA.COLUMNS
                WHERE table_name = '{table}'
                """
                schema_result, error = await conn.query(schema_query)

                if error:
                    self._logger.error(f"Schema query error: {error}")
                    raise ConnectionError(
                        f"Error getting table schema: {error}"
                    )

                schema_result_list = list(schema_result) if schema_result else []

                # Create a dictionary with the type of each column using the list
                column_types = {row['column_name']: row['data_type'] for row in schema_result_list}

                # Create temporary table
                temp_table = f"{table}_temp_{int(time.time())}"
                create_temp_query = f"""
                CREATE TABLE `{schema}.{temp_table}`
                AS SELECT * FROM `{schema}.{table}` WHERE 1=0
                """
                _, create_error = await conn.query(create_temp_query)

                if create_error:
                    self._logger.error(f"Failed to create temporary table: {create_error}")
                    return await self._default_write(conn, table, schema, data, on_conflict)

                try:
                    # Load data into temporary table
                    load_result = await self._default_write(conn, temp_table, schema, data, 'append')

                    if not load_result:
                        self._logger.error("Failed to load data into temporary table")
                        return await self._default_write(conn, table, schema, data, on_conflict)

                    # Add a delay to ensure the data has been loaded
                    self._logger.debug(f"Waiting 2 seconds for data to be fully loaded...")
                    await asyncio.sleep(2)

                    # Build MERGE statement
                    merge_keys = " AND ".join([f"T.{key} = S.{key}" for key in pk])

                    # Build SET clause with special handling for types
                    set_clause = []
                    for col in data.columns:
                        if col not in pk:
                            col_type = column_types.get(col, 'STRING')
                            if col_type == 'JSON':
                                set_clause.append(f"{col} = TO_JSON_STRING(S.{col})")
                            else:
                                set_clause.append(f"{col} = S.{col}")

                    set_clause = ", ".join(set_clause)

                    # Build INSERT clause
                    insert_columns = ", ".join(data.columns)
                    source_columns = ", ".join([f"S.{col}" for col in data.columns])

                    merge_query = f"""
                    MERGE `{schema}.{table}` T
                    USING `{schema}.{temp_table}` S
                    ON {merge_keys}
                    WHEN MATCHED THEN
                        UPDATE SET {set_clause}
                    WHEN NOT MATCHED THEN
                        INSERT({insert_columns})
                        VALUES({source_columns})
                    """

                    # Try to execute the MERGE and capture detailed information
                    try:
                        result, error = await conn.query(merge_query)

                        # Try to access attributes of the result
                        if result:
                            try:
                                if hasattr(result, 'num_dml_affected_rows'):
                                    self._logger.info(f"Affected rows: {result.num_dml_affected_rows}")
                                elif hasattr(result, '__iter__'):
                                    first_row = next(iter(result), None)
                                    self._logger.info(f"First row of result: {first_row}")
                            except Exception as result_ex:
                                self._logger.error(f"Error examining result object: {result_ex}")

                    except Exception as query_ex:
                        self._logger.error(f"Exception during conn.query execution: {query_ex}")
                        self._logger.error(f"Exception type: {type(query_ex)}")
                        raise

                    if error:
                        self._logger.error(f"Error executing MERGE: {error}")
                        raise ConnectionError(
                            f"Error executing MERGE: {error}"
                        )

                    self._logger.info(f"MERGE executed successfully")

                    return result

                finally:
                    await conn.query(f"DROP TABLE IF EXISTS `{schema}.{temp_table}`")

            except Exception as e:
                self._logger.error(f"Error writing to BigQuery: {e}")
                import traceback
                self._logger.error(f"Traceback: {traceback.format_exc()}")
                raise

    async def _default_write(self, conn, table, schema, data, on_conflict):
        """Default write behavior without MERGE."""
        self._logger.debug(
            "BigQuery write method: use_pandas=False, using load_table_from_json"
        )
        use_pandas = isinstance(data, pd.DataFrame)
        try:
            return await conn.write(
                data=data,
                table_id=table,
                dataset_id=schema,
                if_exists=on_conflict,
                use_pandas=use_pandas
            )
        except Exception as e:
            self._logger.error(f"Error writing to BigQuery on Table {schema}.{table}: {e}")
            raise ConnectionError(
                f"Error writing to BigQuery on Table {schema}.{table}: {e}"
            ) from e
