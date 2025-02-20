from typing import Union
from collections.abc import Iterable
import pandas as pd
import time
import logging
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
        self._logger = logging.getLogger(f'DB.{self.__class__.__name__.lower()}')

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
            can_merge = (use_merge and isinstance(data, pd.DataFrame) 
                        and on_conflict == 'replace' and pk and len(pk) > 0)
            
            if can_merge:
                try:
                    # Verificar si la tabla existe y tiene datos
                    check_query = f"SELECT COUNT(*) as count FROM `{schema}.{table}`"
                    result, error = await conn.query(check_query)
                    if error or not result:
                        return await self._default_write(conn, table, schema, data, on_conflict)

                    # Obtener el schema de la tabla original
                    schema_query = f"""
                    SELECT column_name, data_type 
                    FROM {schema}.INFORMATION_SCHEMA.COLUMNS 
                    WHERE table_name = '{table}'
                    """
                    schema_result, error = await conn.query(schema_query)
                    if error:
                        raise Exception(f"Error getting table schema: {error}")
                    
                    # Crear un diccionario con los tipos de columnas
                    column_types = {row['column_name']: row['data_type'] 
                                  for row in schema_result}

                    # Crear tabla temporal
                    temp_table = f"{table}_temp_{int(time.time())}"
                    create_temp_query = f"""
                    CREATE TABLE `{schema}.{temp_table}`
                    AS SELECT * FROM `{schema}.{table}` WHERE 1=0
                    """
                    await conn.query(create_temp_query)

                    try:
                        # Cargar datos en tabla temporal
                        await self._default_write(conn, temp_table, schema, data, 'append')

                        # Construir MERGE statement
                        merge_keys = " AND ".join([f"T.{key} = S.{key}" for key in pk])
                        
                        # Construir SET clause con manejo especial de tipos
                        set_clause = []
                        for col in data.columns:
                            if col not in pk:
                                col_type = column_types.get(col, 'STRING')
                                if col_type == 'JSON':
                                    set_clause.append(f"{col} = TO_JSON_STRING(S.{col})")
                                elif col_type == 'STRING':
                                    # Para columnas string que podrían contener JSON
                                    set_clause.append(f"{col} = S.{col}")
                                else:
                                    set_clause.append(f"{col} = S.{col}")
                        
                        set_clause = ", ".join(set_clause)

                        # Construir INSERT clause
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
                        result, error = await conn.query(merge_query)
                        
                        if error:
                            raise Exception(f"Error executing MERGE: {error}")
                        
                        return result

                    finally:
                        # Limpiar tabla temporal
                        await conn.query(f"DROP TABLE IF EXISTS `{schema}.{temp_table}`")

                except Exception as e:
                    self._logger.warning(f"MERGE operation failed, falling back to default write: {e}")
                    return await self._default_write(conn, table, schema, data, on_conflict)
            else:
                return await self._default_write(conn, table, schema, data, on_conflict)

    async def _default_write(self, conn, table, schema, data, on_conflict):
        """Default write behavior without MERGE."""
        return await conn.write(
            data=data,
            table_id=table,
            dataset_id=schema,
            if_exists=on_conflict,
            use_pandas=False
        )
