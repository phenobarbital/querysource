from typing import Union
from collections.abc import Iterable
import pandas as pd
import time
import json
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

    async def write(
        self,
        table: str,
        schema: str,
        data: Union[pd.DataFrame, Iterable],
        on_conflict: str = 'append',
        pk: list = None
    ):
        """Write data to BigQuery with MERGE support for upserts.
        
        Args:
            table: Table name
            schema: Dataset name
            data: DataFrame or Iterable to write
            on_conflict: How to handle conflicts ('append' or 'replace')
            pk: List of primary key columns for MERGE
        """
        if not self._connection:
            self.default_connection()
        
        async with await self._connection.connection() as conn:
            # Solo intentamos MERGE si:
            # 1. Los datos son un DataFrame
            # 2. on_conflict es 'replace'
            # 3. Tenemos primary keys definidas
            if isinstance(data, pd.DataFrame) and on_conflict == 'replace' and pk:
                # Check if table exists and has data
                try:
                    check_query = f"SELECT COUNT(*) as count FROM `{schema}.{table}`"
                    result, error = await conn.query(check_query)
                    if error:
                        # Si la tabla no existe, hacer append normal
                        self._logger.info(f"Table {schema}.{table} does not exist, performing initial load...")
                        return await conn.write(
                            data=data.to_dict('records'),
                            table_id=table,
                            dataset_id=schema,
                            if_exists='append'
                        )

                    # Convertir RowIterator a lista y obtener el primer resultado
                    rows = list(result)
                    if not rows:
                        # Si no hay resultados, la tabla está vacía
                        self._logger.info(f"Table {schema}.{table} is empty, performing initial load...")
                        return await conn.write(
                            data=data.to_dict('records'),
                            table_id=table,
                            dataset_id=schema,
                            if_exists='append'
                        )

                    count = rows[0]['count']
                    if count == 0:
                        # Si la tabla está vacía, hacer append normal
                        self._logger.info(f"Table {schema}.{table} is empty, performing initial load...")
                        return await conn.write(
                            data=data.to_dict('records'),
                            table_id=table,
                            dataset_id=schema,
                            if_exists='append'
                        )

                    # Si llegamos aquí, la tabla existe y tiene datos, procedemos con MERGE
                    self._logger.info(f"Table {schema}.{table} exists with {count} rows, performing MERGE...")
                    
                    # Detect JSON columns
                    json_columns = set()
                    for col in data.columns:
                        if data[col].dtype == 'object':
                            sample = data[col].dropna().head(1)
                            if not sample.empty:
                                value = sample.iloc[0]
                                if isinstance(value, (dict, list)):
                                    json_columns.add(col)
                                elif isinstance(value, str):
                                    if (value.startswith('{') and value.endswith('}')) or \
                                       (value.startswith('[') and value.endswith(']')):
                                        json_columns.add(col)

                    # Obtener el schema de la tabla original
                    table_schema_query = f"""
                    SELECT 
                        column_name, 
                        data_type 
                    FROM {schema}.INFORMATION_SCHEMA.COLUMNS 
                    WHERE table_name = '{table}'
                    """
                    schema_result, error = await conn.query(table_schema_query)
                    if error:
                        raise Exception(f"Error getting table schema: {error}")

                    # Crear tabla temporal con el mismo schema
                    temp_table = f"{table}_temp_{int(time.time())}"
                    create_temp_query = f"""
                    CREATE TABLE `{schema}.{temp_table}`
                    AS SELECT * FROM `{schema}.{table}` WHERE 1=0
                    """
                    result, error = await conn.query(create_temp_query)
                    if error:
                        raise Exception(f"Error creating temp table: {error}")

                    # Ahora cargar los datos en la tabla temporal
                    records = data.to_dict('records')
                    await conn.write(
                        data=records,
                        table_id=temp_table,
                        dataset_id=schema,
                        if_exists='append'  # Usamos append porque la tabla ya existe
                    )

                    # Construct MERGE statement
                    merge_keys = " AND ".join([f"T.{key} = S.{key}" for key in pk])
                    set_clause = []
                    for col in data.columns:
                        if col not in pk:
                            if col in json_columns:
                                # Para columnas que ya son dict/list, necesitamos convertir a JSON string primero
                                set_clause.append(f"{col} = PARSE_JSON(TO_JSON_STRING(S.{col}))")
                            else:
                                set_clause.append(f"{col} = S.{col}")
                    
                    set_clause = ", ".join(set_clause)

                    merge_query = f"""
                    MERGE `{schema}.{table}` T
                    USING `{schema}.{temp_table}` S
                    ON {merge_keys}
                    WHEN MATCHED THEN
                        UPDATE SET {set_clause}
                    WHEN NOT MATCHED THEN
                        INSERT ROW
                    """

                    self._logger.debug(f"MERGE Query: {merge_query}")

                    try:
                        result, error = await conn.query(merge_query)
                        if error:
                            raise Exception(f"Error executing MERGE: {error}")
                            
                        self._logger.info(f"Executed MERGE on {schema}.{table}")
                        
                        # Clean up temp table
                        await conn.query(f"DROP TABLE `{schema}.{temp_table}`")
                        
                        return result
                    except Exception as e:
                        try:
                            await conn.query(f"DROP TABLE `{schema}.{temp_table}`")
                        except:
                            pass
                        raise e
                except Exception as e:
                    raise Exception(f"Error during MERGE operation: {e}")
            else:
                # Default write behavior
                result = await conn.write(
                    data=data,
                    table_id=table,
                    dataset_id=schema,
                    if_exists=on_conflict,
                    use_pandas=False
                )
                return result
