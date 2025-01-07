import asyncio
from typing import Any, Union
from collections.abc import Callable
from abc import abstractmethod
import pandas as pd
from pandas import DataFrame
from ....exceptions import (
    DriverError,
    QueryException,
    DataNotFound
)
from .abstract import AbstractTransform


class tPandas(AbstractTransform):
    """
    tPandas

        Overview

        The tPandas class is an abstract interface for performing various data transformations on Pandas DataFrames.
        It provides foundational methods and structure for components that need to apply transformations, merges, or other
        DataFrame operations within a task.

        This interface provides methods to initialize, transform, and debug Pandas DataFrame operations.
        Concrete implementations using `tPandas` can define specific transformations. On execution, metrics
        for rows and columns are recorded, and any transformation errors or data mismatches are raised as exceptions
        with detailed error messages for effective debugging.
    """  # noqa
    def __init__(self, data: Union[dict, DataFrame], **kwargs) -> None:
        """Init Method."""
        self.type: str = None
        self.condition: str = ''
        # Pandas Arguments:
        self.pd_args = kwargs.pop("pd_args", {})
        super(tPandas, self).__init__(data, **kwargs)

    @abstractmethod
    async def _run(self) -> DataFrame:
        """
        Abstract method to run the transformation.
        Returns:
            DataFrame: The transformed DataFrame.
        """
        pass  # pragma: no cover"""

    async def run(self):
        await self.start()
        try:
            df = await self._run()
            if df.empty:
                raise DataNotFound(
                    f"Data not Found over {self.__class__.__name__}"
                )
            return df
        except DataNotFound:
            raise
        except (ValueError, KeyError) as err:
            raise DriverError(
                f"{self.__class__.__name__} Error: {err!s}"
            ) from err
        except Exception as err:
            raise QueryException(
                f"{self.__class__.__name__} Exception {err!s}"
            ) from err
