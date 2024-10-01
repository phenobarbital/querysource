from collections.abc import Callable
import pandas as pd
from .....exceptions import OutputError
from .abstract import AbstractOutput
from .....interfaces.databases.rethink import RethinkDB


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
        result = await self.write(
            table,
            schema,
            data,
            on_conflict=on_conflict
        )
        return result
