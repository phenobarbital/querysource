import pandas as pd
from navconfig.logging import logging
from ....exceptions import (
    DataNotFound,
    DriverError,
    QueryException
)

class Melt:
    def __init__(self, data: dict, **kwargs) -> None:
        try:
            self._id_vars = kwargs['id']
            del kwargs['id']
        except KeyError:
            pass
        self._backend = 'pandas'
        self.data = data
        for k, v in kwargs.items():
            setattr(self, k, v)

    async def start(self):
        for _, data in self.data.items():
            ## TODO: add support for polars and datatables
            if isinstance(data, pd.DataFrame):
                self._backend = 'pandas'
            else:
                raise DriverError(
                    f'Wrong type of data for JOIN, required Pandas dataframe: {type(data)}'
                )
        try:
            self.df1 = self.data.pop(self.using)
        except (KeyError, IndexError) as ex:
            raise DriverError(
                f"Missing LEFT Dataframe on Data: {self.data[self.using]}"
            ) from ex
        ### check for empty
        if self.df1.empty:
            raise DataNotFound(
                f"Empty Main {self.using} Dataframe"
            )
        try:
            self.df2 = self.data.popitem()[1]
        except (KeyError, IndexError) as ex:
            raise DriverError(
                "Missing Melted Dataframe"
            ) from ex

    async def run(self):
        await self.start()
        args = {}
        if hasattr(self, 'args') and isinstance(self.args, dict):
            args = {**args, **self.args}
        try:
            # "Melt" Original DataFrame to prepare for "crosstab"
            df_melt = self.df1.melt(
                id_vars=self._id_vars,
                **args
            )
            # Join the melted DataFrame with the courses DataFrame
            df_joined = df_melt.join(
                self.df2.set_index('column_name'), on='column_name'
            )
            # Drop rows where course_date is null
            df = df_joined.dropna(subset=['course_date'])
            print('::: Printing Column Information === ')
            for column, t in df.dtypes.items():
                print(column, '->', t, '->', df[column].iloc[0])
            return df
        except DataNotFound:
            raise
        except (ValueError, KeyError) as err:
            raise QueryException(
                f'Cannot Join with missing Column: {err!s}'
            ) from err
        except Exception as err:
            raise QueryException(
                f"Unknown JOIN error {err!s}"
            ) from err
