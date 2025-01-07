from typing import Union
from pandas import DataFrame
from ....exceptions import (
    DriverError,
    QueryException,
    DataNotFound
)
from .tPandas import tPandas

class tOrder(tPandas):
    """
        tOrder

        Overview

        The `tOrder` class is a component designed to order a Pandas DataFrame by a specified column.
        It allows sorting the DataFrame either in ascending or descending order based on the specified column.

        Properties

        .. table:: Properties
        :widths: auto

        +------------------+----------+-----------+-----------------------------------------------------------------------------------+
        | Name             | Required | Type      | Description                                                                       |
        +------------------+----------+-----------+-----------------------------------------------------------------------------------+
        | columns          | Yes      | str       | The name of the column to sort the DataFrame by.                                  |
        +------------------+----------+-----------+-----------------------------------------------------------------------------------+
        | ascending        | No       | bool      | Specifies whether to sort the DataFrame in ascending order. Defaults to True.     |
        +------------------+----------+-----------+-----------------------------------------------------------------------------------+

        Return
           The dataframe ordinated by the column give it in the order_by either ascending or descending.

    """  # noqa

    def __init__(self, data: Union[dict, DataFrame], **kwargs) -> None:
        """Init Method."""
        self._column: Union[str, list] = kwargs.pop("columns", None)
        if isinstance(self._column, list):
            ascending = [True for _ in self._column]
        elif isinstance(self._column, str):
            ascending = [True]
            self._column = [self._column]
        self._ascending: Union[bool, list] = kwargs.pop("ascending", ascending)
        self._na_position: str = kwargs.pop("na_position", "last")
        if not self._column:
            raise DriverError(
                "tOrder requires a column for ordering => **columns**"
            )
        super(tOrder, self).__init__(data, **kwargs)

    async def _run(self):
        try:
            # Check if the specified column exists in the DataFrame
            columns = self.data.columns
            for col in self._column:
                if col not in columns:
                    self.logger.warning(
                        f"The column '{self._column}' does not exist in the DataFrame."
                    )
                    return self.data  # Return the unsorted DataFrame
                # Check if the specified column is empty
                if self.data[self._column].empty:
                    self.logger.warning(
                        f"The column '{self._column}' is empty."
                    )
                    return self.data
            # Sort the DataFrame by the specified column
            return self.data.sort_values(
                by=self._column,
                ascending=self._ascending,
                na_position=self._na_position,
                **self.pd_args
            ).reset_index(drop=True)
        except Exception as err:
            raise QueryException(
                f"Generic Error on Data: error: {err}"
            ) from err
