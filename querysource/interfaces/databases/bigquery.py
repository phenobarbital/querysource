from typing import Union
from collections.abc import Iterable
import pandas as pd
# Default RethinkDB connection parameters
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
        on_conflict: str = 'append'
    ):
        if not self._connection:
            self.default_connection()
        async with await self._connection.connection() as conn:
            result = await conn.write(
                data=data,
                table_id=table,
                dataset_id=schema,
                if_exists=on_conflict,
                use_pandas=False  # Not using stream API
            )
            return result
