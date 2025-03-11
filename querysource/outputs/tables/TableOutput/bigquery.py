from collections.abc import Callable
import pandas as pd
from ....exceptions import OutputError
from ....interfaces.databases.bigquery import BigQuery
from .abstract import AbstractOutput


class BigQueryOutput(AbstractOutput, BigQuery):
    """
    BigQueryOutput.

    Class for writing output to BigQuyery database.

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
        BigQuery.__init__(
            self, **kwargs
        )
        self._external: bool = True
        self.use_merge = kwargs.get('use_merge', False)  # Nuevo parámetro

    async def db_upsert(
        self,
        table: str,
        schema: str,
        data: pd.DataFrame,
        on_conflict: str = 'replace',
        pk: list = None,
        use_merge: bool = None  # Cambiar el default a None
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
            use_merge = False

        return await self.write(
            table,
            schema,
            data,
            on_conflict=on_conflict,
            pk=pk,
            use_merge=use_merge
        )

    def connect(self):
        """
        Connect to BigQuery
        """
        if not self._connection:
            self.default_connection()

    async def close(self):
        """
        Close Database connection.

        we don't need to explicitly close the connection.
        """
        pass
