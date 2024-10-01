from abc import ABCMeta, abstractmethod
from collections.abc import Callable, Awaitable
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from navconfig.logging import logging
from .....exceptions import OutputError


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
        self._connection: Awaitable = None
        self._driver: str = kwargs.get('driver', 'pg')
        if self._external is False:
            try:
                self._engine = create_engine(dsn, echo=False, poolclass=NullPool)
            except Exception as err:
                logging.exception(err, stack_info=True)
                raise OutputError(
                    message=f"Connection Error: {err}"
                ) from err

    def engine(self):
        return self._engine

    @property
    def is_external(self) -> bool:
        return self._external

    def close(self):
        """Closing Operations."""
        try:
            self._engine.dispose()
        except Exception as err:
            logging.error(err)

    def result(self):
        return self._results

    @property
    def columns(self):
        return self._columns

    @columns.setter
    def columns(self, columns: list):
        self._columns = columns

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
