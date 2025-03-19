from typing import List, Dict, Union, Optional, Any
from collections.abc import Callable
import pandas as pd
from ....interfaces.databases.mongo import MongoDB
from .abstract import AbstractOutput


class MongoDBOutput(AbstractOutput, MongoDB):
    """
    MongoDBOutput.

    Class for writing output to MongoDB database.

    Using External.
    """
    def __init__(
        self,
        parent: Callable,
        dsn: str = None,
        do_update: bool = True,
        external: bool = True,
        **kwargs
    ) -> None:
        # External: using a non-SQLAlchemy engine (outside Pandas)
        AbstractOutput.__init__(
            self, parent, dsn, do_update, external, **kwargs
        )
        MongoDB.__init__(
            self, **kwargs
        )
        self._external: bool = True

    async def db_upsert(
        self,
        table: str,
        schema: str,
        data: pd.DataFrame,
        on_conflict: str = 'replace'
    ):
        """
        Execute an Upsert of Data using "write" method

        Parameters
        ----------
        table : table name
        schema : database schema
        data : Iterable or pandas dataframe to be inserted.
        """
        if self._do_update is False:
            on_conflict = 'append'
        return await self.write(
            table=table,
            schema=schema,
            data=data,
            on_conflict=on_conflict
        )

    def connect(self):
        """
        Connect to MongoDB
        """
        if not self._connection:
            self.default_connection()

    async def close(self):
        """
        Close Database connection.

        we don't need to explicitly close the connection.
        """
        pass

    async def write(
        self,
        table: str,
        schema: str,
        data: Union[List[Dict], pd.DataFrame],
        on_conflict: Optional[str] = 'replace',
        pk: List[str] = None
    ):
        """
        Execute an statement for writing data

        Parameters
        ----------
        table : table name
        schema : database schema
        data : Iterable or pandas dataframe to be inserted.
        on_conflict : str, default 'replace'
            Conflict resolution strategy
        pk : list of str, default None
            Primary key columns
        """
        return await self.self._connection.write(
            data,
            table=table,
            schema=schema,
            if_exists=on_conflict,
            pk=pk,
            use_pandas=True
        )
