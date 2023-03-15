from typing import Union
from abc import abstractmethod
import pandas as pd
from navconfig.logging import logging
from querysource.exceptions import (
    DataNotFound,
    DriverError
)

class AbstractTransform:
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        self._backend = 'pandas'
        self.data = data
        self.logger = logging.getLogger(f'QS.Transform.{self.__class__.__name__}')
        for k, v in kwargs.items():
            setattr(self, k, v)

    def colum_info(self, df):
        print('::: Printing Column Information === ')
        for column, t in df.dtypes.items():
            print(column, '->', t, '->', df[column].iloc[0])

    async def start(self):
        ### TODO: making transformations over list of dataframes
        if isinstance(self.data, dict):
            for _, data in self.data.items():
                ## TODO: add support for polars and datatables
                if isinstance(data, pd.DataFrame):
                    self._backend = 'pandas'
                    if data.empty:
                        raise DataNotFound(
                            "Empty Dataframe"
                        )
                else:
                    raise DriverError(
                        f'Wrong type of data: required a Pandas dataframe: {type(data)}'
                    )
        elif not isinstance(self.data, pd.DataFrame):
            raise DriverError(
                f'Wrong type of data, required a Pandas dataframe: {type(data)}'
            )

    @abstractmethod
    async def run(self):
        pass
