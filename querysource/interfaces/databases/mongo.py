from typing import Union
from collections.abc import Iterable
import pandas as pd
# Default MongoDB connection parameters
from ...conf import (
    MONGO_DRIVER,
    MONGO_HOST,
    MONGO_PORT,
    MONGO_DATABASE,
    MONGO_USER,
    MONGO_PASSWORD,
    DOCUMENTDB_HOSTNAME,
    DOCUMENTDB_PORT,
    DOCUMENTDB_DATABASE,
    DOCUMENTDB_USERNAME,
    DOCUMENTDB_PASSWORD,
    DOCUMENTDB_TLSFILE,
)
from .abstract import AbstractDB


class MongoDB(AbstractDB):
    """MongoDB.

    Class for writing data to a MongoDB Database.
    """
    _name: str = "MongoDB"

    def __init__(self, *args, **kwargs):
        self.use_pandas: bool = kwargs.get('use_pandas', True)
        super().__init__(*args, **kwargs)
        self.db_credentials: dict = {
            "host": MONGO_HOST,
            "port": MONGO_PORT,
            "username": MONGO_USER,
            "password": MONGO_PASSWORD,
            "database": MONGO_DATABASE
        }
        self._driver: str = MONGO_DRIVER

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
            return await conn.write(
                data=data,
                collection=table,
                database=schema,
                if_exists=on_conflict,
                use_pandas=self.use_pandas
            )


class DocumentDB(MongoDB):
    """DocumentDB.

    Class for writing data to a AWS DocumentDB Database.
    """
    _name: str = "DocumentDB"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db_credentials: dict = {
            "host": DOCUMENTDB_HOSTNAME,
            "port": DOCUMENTDB_PORT,
            "username": DOCUMENTDB_USERNAME,
            "password": DOCUMENTDB_PASSWORD,
            "database": DOCUMENTDB_DATABASE,
            "dbtype": "documentdb",
            "ssl": True,
            "tlsCAFile": DOCUMENTDB_TLSFILE,
        }
        self._driver: str = MONGO_DRIVER
