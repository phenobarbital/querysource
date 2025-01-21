"""
Operators.

This module contains the abstract class for operators.

Operators are the main building blocks of a query. They are responsible for making basic transformations
as Join, Melt, Concat or Filter.
"""
import pandas as pd
import modin.pandas as mpd
from abc import ABC, abstractmethod
from ....exceptions import QueryException


class AbstractOperator(ABC):
    """AbstractOperator.

    Abstract Class for Multi-Query Operators.
    """
    def __init__(self, data: dict, **kwargs) -> None:
        self._backend = kwargs.get('backend', 'pandas')
        # Use Modin as backend if available
        self._pd = mpd if self._backend == 'modin' else pd
        self.data = data
        for k, v in kwargs.items():
            setattr(self, k, v)

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            raise QueryException(
                f"Operator Error: {exc_value!s}"
            ) from exc_value
        await self.close()

    @abstractmethod
    async def start(self):
        """Start the Operator, useful for making validations before execution.
        """

    @abstractmethod
    async def run(self):
        """Run the Operator.
        """

    async def close(self):
        """Close the Operator.
        """
        pass

    def _print_info(self, df: pd.DataFrame):
        print('::: Printing Column Information === ')
        for column, t in df.dtypes.items():
            print(column, '->', t, '->', df[column].iloc[0])
        print()
