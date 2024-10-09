from typing import Optional, Union
from collections.abc import Awaitable, Iterable
from abc import ABCMeta, abstractmethod
import asyncio
import pandas as pd
from asyncdb import AsyncDB
from ...exceptions import DriverError


class AbstractDB(metaclass=ABCMeta):
    """AbstractDriver.

    Abstract Database Driver for several operations including TableOutput.
    """
    def __init__(self, *args, **kwargs):
        self._connection: Optional[Awaitable] = None
        self.chunksize: int = kwargs.get('chunksize', 100)
        self.db_credentials: dict = {}
        self._driver: str = kwargs.get('driver', 'pg')

    def default_connection(self):
        """default_connection.

        Default Connection.
        """
        try:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                raise RuntimeError(
                    f"{self._name}: No event loop is running."
                )
            self._connection = AsyncDB(
                self._driver,
                params=self.db_credentials,
                loop=loop
            )
            return self._connection
        except Exception as err:
            raise DriverError(
                f"Error configuring {self._name} Connection: {err!s}"
            ) from err

    @abstractmethod
    async def write(
        self,
        table: str,
        schema: str,
        data: Union[pd.DataFrame, Iterable],
        on_conflict: str = 'replace'
    ):
        """write.

        Writing data to AsyncDB Database using "write" method.
        """
        pass
