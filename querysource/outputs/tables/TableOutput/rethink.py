from typing import List, Dict, Union, Optional, Any
from collections.abc import Callable
import pandas as pd
from ....exceptions import OutputError
from ....interfaces.databases.rethink import RethinkDB
from .abstract import AbstractOutput


class RethinkOutput(AbstractOutput, RethinkDB):
    """
    RethinkOutput.

    Class for writing output to rethinkdb database.

    Used by Pandas to_sql statement.
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
        RethinkDB.__init__(
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
            table,
            schema,
            data,
            on_conflict=on_conflict
        )

    def connect(self):
        """
        Connect to DocumentDB
        """
        if not self._connection:
            self.default_connection()

    async def close(self):
        """
        Close Database connection.
        """
        if self._connection:
            self._connection.close()
            self._connection = None
        return True

    def write(
        self,
        table: str,
        schema: str,
        data: Union[List[Dict], Any],
        on_conflict: Optional[str] = 'replace',
        pk: List[str] = None
    ):
        raise NotImplementedError("Method not implemented")
