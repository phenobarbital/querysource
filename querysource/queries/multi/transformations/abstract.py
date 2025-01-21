from typing import Union
from abc import abstractmethod
import pandas as pd
from navconfig.logging import logging
from ....exceptions import (
    DataNotFound,
    DriverError,
    QueryException
)


class AbstractTransform:
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        self._backend = 'pandas'
        self.data = data
        self.logger = logging.getLogger(f'QS.Transform.{self.__class__.__name__}')
        for k, v in kwargs.items():
            setattr(self, k, v)

    def colum_info(self, df):
        print(df.head())
        print('::: Printing Column Information === ')
        for column, t in df.dtypes.items():
            print(column, '->', t, '->', df[column].iloc[0])

    async def start(self):
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
                f'Wrong type of data, required a Pandas dataframe: {type(data)}'
            )

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            raise QueryException(
                f"Operator Error: {exc_value!s}"
            ) from exc_value
        await self.close()

    async def close(self):
        pass

    @abstractmethod
    async def run(self):
        pass
