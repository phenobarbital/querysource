"""
Components.

This module contains the abstract class for components.

Components are optional building blocks of a multi-query.
They are responsible for making complex transformations and operations not covered by operators.
"""
import pandas as pd

from abc import ABC, abstractmethod
from ....exceptions import QueryException


class AbstractComponent(ABC):
    """AbstractComponent.

    Abstract Class for Multi-Query Components.
    """
    def __init__(self, data: dict, **kwargs) -> None:
        self.data = data
        for k, v in kwargs.items():
            setattr(self, k, v)

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            raise QueryException(
                f"Component Error: {exc_value!s}"
            ) from exc_value
        await self.close()

    @abstractmethod
    async def start(self):
        """Start the Component, useful for making validations before execution.
        """

    @abstractmethod
    async def run(self):
        """Run the Component.
        """

    async def close(self):
        """Close the Component.
        """
        pass

    def _print_info(self, df: pd.DataFrame):
        print('::: Printing Column Information === ')
        for column, t in df.dtypes.items():
            print(column, '->', t, '->', df[column].iloc[0])
        print()
