from typing import Union
from collections.abc import Iterable
import pandas as pd
# Default RethinkDB connection parameters
from ...conf import (
    RT_DRIVER,
    RT_HOST,
    RT_PORT,
    RT_USER,
    RT_PASSWORD,
    RT_DATABASE
)
from .abstract import AbstractDB


class RethinkDB(AbstractDB):
    """RethinkDB.

    Class for writing data to a RethinkDB Database.
    """
    _name: str = "RethinkDB"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db_credentials: dict = {
            "host": RT_HOST,
            "port": int(RT_PORT),
            "db": RT_DATABASE,
            "user": RT_USER,
            "password": RT_PASSWORD
        }
        self._driver: str = RT_DRIVER
        self._connection = None

    async def write(
        self,
        table: str,
        schema: str,
        data: Union[pd.DataFrame, Iterable],
        on_conflict: str = 'replace'
    ):
        if not self._connection:
            self.default_connection()
        async with await self._connection.connection() as conn:
            await conn.use(schema)
            result = await self._connection.write(
                table=table,
                data=data,
                batch_size=self.chunksize,
                on_conflict=on_conflict,
                changes=True,
                durability="soft"
            )
            return result
