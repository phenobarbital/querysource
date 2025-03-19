from abc import ABCMeta, abstractmethod
from collections.abc import Callable, Awaitable
from typing import Dict, List, Optional, Union
from navconfig.logging import logging
from pandas import DataFrame

class AbstractOutput(metaclass=ABCMeta):
    """
    AbstractOutput.

    Base class for all to_sql pandas Outputs.
    """
    def __init__(
        self,
        parent: Callable,
        dsn: str = None,
        do_update: bool = True,
        only_update: bool = False,
        external: bool = False,
        **kwargs
    ) -> None:
        # External: using a non-SQLAlchemy engine (outside Pandas)
        self._external: bool = external
        self._engine: Callable = None
        self._parent = parent
        self._results: list = []
        self._columns: list = []
        self._do_update: bool = do_update
        self._only_update: bool = only_update
        self._connection: Awaitable = None
        self._driver: str = kwargs.get('driver', 'pg')
        self.logger = logging.getLogger(__name__)

    def engine(self):
        return self._engine

    def get_connection(self):
        return self._connection

    @property
    def is_external(self) -> bool:
        return self._external

    def result(self):
        return self._results

    @property
    def columns(self):
        return self._columns

    @columns.setter
    def columns(self, columns: list):
        self._columns = columns

    @abstractmethod
    def connect(self):
        """
        Connect to Database
        """
        pass

    @abstractmethod
    def db_upsert(self, table, conn, keys, data_iter):
        """
        Execute SQL statement for upserting data

        Parameters
        ----------
        table : pandas.io.sql.SQLTable
        conn : sqlalchemy.engine.Engine or sqlalchemy.engine.Connection
        keys : list of str of Column names
        data_iter : Iterable that iterates the values to be inserted
        """
        pass

    @abstractmethod
    def write(
        self,
        table: str,
        schema: str,
        data: Union[List[Dict], DataFrame],
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
        pass

    @abstractmethod
    async def close(self):
        """
        Close Database connection (if available)
        """
        pass

    async def open(self):
        """
        Open Database connection.
        """
        pass

    async def __aenter__(self):
        """
        Async Enter method.
        """
        self.connect()
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Async Exit method.
        """
        await self.close()
