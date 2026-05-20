"""
Components.

This module contains the abstract class for components.

Components are optional building blocks of a multi-query.
They are responsible for making complex transformations and operations not covered by operators.
"""
import pandas as pd

from abc import abstractmethod
from ....exceptions import QueryException
from ..abstract import AbstractMulti


class AbstractComponent(AbstractMulti):
    """AbstractComponent.

    Abstract Class for Multi-Query Components.

    Usage: Base class for optional complex transformation/operation steps in a MultiQuery pipeline.
    """

    _category = "Components"

    def __init__(self, data: dict, **kwargs) -> None:
        super().__init__(data, **kwargs)

    @abstractmethod
    async def start(self):
        """Start the Component, useful for making validations before execution.
        """

    @abstractmethod
    async def run(self):
        """Run the Component.
        """
