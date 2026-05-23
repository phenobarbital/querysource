from pandas import DataFrame
from ....exceptions import (
    DataNotFound,
    DriverError,
    QueryException
)
from .abstract import AbstractOperator

class Melt(AbstractOperator):
    """Unpivot a DataFrame from wide format to long format.

    Melts the main DataFrame (identified by ``using``) by turning selected
    columns into rows, then joins the result with a metadata DataFrame.

    Usage: Use in a MultiQuery pipeline to reshape wide DataFrames into long
    (tidy) format for further analysis or visualization.

    Attributes:
        id: Column(s) to use as identifier variables (kept fixed during melt).
        na_cols: Column name(s) from which to drop null rows after the join.
        using: Name of the primary DataFrame to melt (required).

    Example:
        {
            "Melt": {
                "using": "wide_data",
                "id": "date",
                "na_cols": "value"
            }
        }
    """

    def __init__(self, data: dict, **kwargs) -> None:
        self._id_vars = kwargs.pop('id', None)
        self._na_cols = kwargs.pop('na_cols', None)
        super(Melt, self).__init__(data, **kwargs)

    async def start(self):
        for _, data in self.data.items():
            ## TODO: add support for polars and datatables
            if isinstance(data, DataFrame):
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
        ### check for emptiness
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
        args = {}
        if hasattr(self, 'args') and isinstance(self.args, dict):
            args = {**args, **self.args}
        try:
            # "Melt" Original DataFrame to prepare for "crosstab"
            df_melt = self.df1.melt(
                id_vars=self._id_vars,
                **args
            )
            # Join the melted DataFrame with the first DataFrame
            df_joined = df_melt.join(
                self.df2.set_index('column_name'), on='column_name'
            )
            # Drop rows where drop_cols is null
            if self._na_cols:
                df = df_joined.dropna(subset=self._na_cols)
            else:
                df = df_joined
            self._print_info(df)
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
