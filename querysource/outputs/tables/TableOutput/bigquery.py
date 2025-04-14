from typing import Union, Dict, List, Optional
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
        use_merge: bool = None
    ):
        """
        Execute an Upsert of Data using "write" method

        Parameters
        ----------
        table : table name
        schema : database schema
        data : Iterable or pandas dataframe to be inserted.
        """
        self._logger.debug(f"Primary keys: {pk}")
        self._logger.debug(f"use_merge parameter: {use_merge}, self.use_merge: {self.use_merge}")
        if isinstance(data, pd.DataFrame):
            self._logger.debug(f"DataFrame columns: {list(data.columns)}")
            self._logger.debug(f"DataFrame shape: {data.shape}")
        
        self.connect()
        
        if self._do_update is False:
            self._logger.debug("do_update is False, setting on_conflict to 'append' and use_merge to False")
            on_conflict = 'append'
            use_merge = False
        elif use_merge is None:
            self._logger.debug(f"use_merge is None, using self.use_merge: {self.use_merge}")
            use_merge = self.use_merge

        self._logger.debug(f"Calling write with use_merge={use_merge}, on_conflict={on_conflict}")
        
        # Asegurarse de que todos los parámetros se pasan correctamente
        result = await BigQuery.write(
            self,
            table,
            schema,
            data,
            on_conflict=on_conflict,
            pk=pk,
            use_merge=use_merge
        )
        
        return result

    def connect(self):
        """
        Connect to BigQuery
        """
        try:
            if not self._connection:
                self.default_connection()
                if not self._connection:
                    raise ConnectionError("Failed to establish connection to BigQuery")
        except Exception as e:
            self._logger.error(f"Error connecting to BigQuery: {e}")
            raise

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
        return await self._connection.write(
            data,
            table_id=table,
            dataset_id=schema,
            if_exists=on_conflict,
            pk=pk,
            use_pandas=True
        )
