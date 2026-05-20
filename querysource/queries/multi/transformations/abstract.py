from typing import Union
from abc import abstractmethod
import pandas as pd
from navconfig.logging import logging
from ....exceptions import (
    DataNotFound,
    DriverError,
    QueryException
)
from ..abstract import AbstractMulti


class AbstractTransform(AbstractMulti):
    """Unified base class for all MultiQuery Transformations.

    Inherits shared boilerplate (init, async context manager, lifecycle) from
    AbstractMulti and adds transform-specific data validation in ``start()``.

    Usage: Base class for all data-transformation steps in a MultiQuery pipeline.
    """

    _category = "Transformations"

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        self._backend = 'pandas'
        self.logger = logging.getLogger(f'QS.Transform.{self.__class__.__name__}')
        super().__init__(data, **kwargs)

    def _print_info(self, df: pd.DataFrame) -> None:
        """Print column type/sample information for a DataFrame."""
        print(df.head())
        print('::: Printing Column Information === ')
        for column, t in df.dtypes.items():
            print(column, '->', t, '->', df[column].iloc[0])

    async def start(self):
        """Validate input data before running the transformation."""
        ### TODO: making transformations over list of dataframes
        if isinstance(self.data, dict):
            for _, data in self.data.items():
                ## TODO: add support for polars and datatables
                if not isinstance(data, pd.DataFrame):
                    raise DriverError(
                        f'Wrong type of data: required a Pandas dataframe: {type(data)}'
                    )
                self._backend = 'pandas'
                if data.empty:
                    raise DataNotFound(
                        "Empty Dataframe"
                    )
        elif not isinstance(self.data, pd.DataFrame):
            raise DriverError(
                f'Wrong type of data, required a Pandas dataframe: {type(self.data)}'
            )

    @abstractmethod
    async def run(self):
        """Execute the transformation. Must be implemented by every concrete subclass."""
