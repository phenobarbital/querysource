from typing import Union, Dict, List, Optional
from collections.abc import Callable
import pandas as pd
import logging
from ....exceptions import OutputError
from ....interfaces.databases.bigquery import BigQuery
from .abstract import AbstractOutput

class BigQueryOutput(BigQuery, AbstractOutput):
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
        only_update: bool = False,
        external: bool = True,
        **kwargs
    ) -> None:
        # External: using a non-SQLAlchemy engine (outside Pandas)
        super().__init__(
            parent, dsn,
            do_update=do_update,
            only_update=only_update,
            external=external,
            **kwargs
        )
        self._external: bool = True
        self._do_update = do_update
        self.use_merge = kwargs.get('use_merge', False)

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
        logging.debug(f"Primary keys: {pk}")
        logging.debug(f"use_merge parameter: {use_merge}, self.use_merge: {self.use_merge}")
        if isinstance(data, pd.DataFrame):
            logging.debug(f"DataFrame columns: {list(data.columns)}")
            logging.debug(f"DataFrame shape: {data.shape}")

        self.connect()

        if self._do_update is False:
            logging.debug("do_update is False, setting on_conflict to 'append' and use_merge to False")
            on_conflict = 'append'
            use_merge = False
        elif use_merge is None:
            logging.debug(f"use_merge is None, using self.use_merge: {self.use_merge}")
            use_merge = self.use_merge

        logging.debug(f"Calling write with use_merge={use_merge}, on_conflict={on_conflict}")

        # Asegurarse de que todos los parámetros se pasan correctamente
        result = await BigQuery.write(
            self,
            table=table,
            schema=schema,
            data=data,
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
            logging.error(f"Error connecting to BigQuery: {e}")
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
        print('HERE >> table: ', table, ' schema: ', schema, ' data: ')
        return await self._connection.write(
            data,
            table_id=table,
            dataset_id=schema,
            if_exists=on_conflict,
            pk=pk,
            use_pandas=True
        )
