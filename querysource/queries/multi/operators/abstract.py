"""
Operators.

This module contains the abstract class for operators.

Operators are the main building blocks of a query. They are responsible for making basic transformations
as Join, Melt, Concat or Filter.
"""
import logging
from abc import abstractmethod

import pandas as pd

from ..abstract import AbstractMulti


class AbstractOperator(AbstractMulti):
    """AbstractOperator.

    Abstract Class for Multi-Query Operators.

    Usage: Base class for all operator steps (Join, Concat, Melt, etc.) in a MultiQuery pipeline.
    """

    _category = "Operators"

    def __init__(self, data: dict, **kwargs) -> None:
        self._backend = kwargs.get('backend', 'pandas')
        # Use Modin as backend if available
        if self._backend == 'modin':
            try:
                import modin.pandas as mpd

                self._pd = mpd
            except ImportError:
                logging.getLogger(__name__).warning(
                    "Modin backend requested but modin is not installed "
                    "(pip install querysource[modin]); falling back to pandas."
                )
                self._backend = 'pandas'
                self._pd = pd
        else:
            self._pd = pd
        super().__init__(data, **kwargs)

    @abstractmethod
    async def start(self):
        """Start the Operator, useful for making validations before execution.
        """

    @abstractmethod
    async def run(self):
        """Run the Operator.
        """
