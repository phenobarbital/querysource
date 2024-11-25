from pandas import DataFrame
from ....exceptions import (
    DriverError,
    QueryException
)
from .abstract import AbstractOperator

class Concat(AbstractOperator):
    """
    Concat to Dataframes in one.
    """
    async def start(self):
        dataset = []
        for _, data in self.data.items():
            if isinstance(data, DataFrame):
                self._backend = 'pandas'
                dataset.append(data)
            else:
                raise DriverError(
                    f'Wrong type of data for Concat, required a Pandas dataframe: {type(data)}'
                )
        self.data = dataset

    async def run(self):
        try:
            df = self._pd.concat(self.data, ignore_index=True)
            self._print_info(df)
            return df
        except (ValueError, KeyError) as err:
            raise QueryException(
                f'Cannot Join with missing Column: {err!s}'
            ) from err
        except Exception as err:
            raise QueryException(
                f"Unknown JOIN error {err!s}"
            ) from err
