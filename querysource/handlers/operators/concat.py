import pandas as pd
from navconfig.logging import logging
from querysource.exceptions import (
    DataNotFound,
    DriverError,
    QueryException
)

class Concat:
    def __init__(self, data: dict, **kwargs) -> None:
        self._backend = 'pandas'
        self.data = data
        for k, v in kwargs.items():
            setattr(self, k, v)

    async def start(self):
        dataset = []
        for _, data in self.data.items():
            ## TODO: add support for polars and datatables
            if isinstance(data, pd.DataFrame):
                self._backend = 'pandas'
                dataset.append(data)
            else:
                raise DriverError(
                    f'Wrong type of data for Concat, required Pandas dataframe: {type(data)}'
                )
        self.data = dataset
        print('dataset', self.data)

    async def run(self):
        await self.start()
        try:
            df = pd.concat(self.data, ignore_index=True)
            print('::: Printing CONCAT Information === ')
            for column, t in df.dtypes.items():
                print(column, '->', t, '->', df[column].iloc[0])
            return df
        except (ValueError, KeyError) as err:
            raise QueryException(
                f'Cannot Join with missing Column: {err!s}'
            ) from err
        except Exception as err:
            raise QueryException(
                f"Unknown JOIN error {err!s}"
            ) from err
