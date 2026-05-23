"""
AbstractMulti — Unified base class for all MultiQuery processing steps.

Provides shared boilerplate (init, async context manager, lifecycle methods)
and introspection classmethods for documentation generation.
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Union

import pandas as pd

from ...exceptions import QueryException
from ._introspect import SchemaIntrospectable

logger = logging.getLogger(__name__)


class AbstractMulti(SchemaIntrospectable, ABC):
    """Unified base class for all MultiQuery processing steps.

    Provides shared boilerplate (kwargs-based init, async context manager,
    lifecycle methods) and introspection classmethods for documentation
    and schema generation.

    Subclasses should set the ``_category`` class attribute to classify
    themselves (e.g. ``"Operators"``, ``"Transformations"``, ``"Components"``).
    """

    _category: str = "Components"

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        """Initialise the processing step.

        Args:
            data: Input data — either a dict of DataFrames or a single DataFrame.
            **kwargs: Arbitrary keyword arguments stored as instance attributes.
        """
        self.data = data
        for k, v in kwargs.items():
            setattr(self, k, v)

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            if exc_type is asyncio.CancelledError:
                await self.close()
                return False  # let cancellation propagate
            raise QueryException(
                f"MultiQuery Error: {exc_value!s}"
            ) from exc_value
        await self.close()

    # ------------------------------------------------------------------
    # Lifecycle methods
    # ------------------------------------------------------------------

    async def start(self):
        """Start the step — called by ``__aenter__``.

        Override in subclasses for pre-run validation.
        """
        pass

    @abstractmethod
    async def run(self):
        """Execute the processing step.

        Must be implemented by every concrete subclass.
        """

    async def close(self):
        """Clean up after the step — called by ``__aexit__``."""
        pass

    # ------------------------------------------------------------------
    # Debug helper
    # ------------------------------------------------------------------

    def _print_info(self, df: pd.DataFrame) -> None:
        """Log column type/sample information for a DataFrame."""
        logger.debug('::: Printing Column Information === ')
        for column, t in df.dtypes.items():
            logger.debug('%s -> %s -> %s', column, t, df[column].iloc[0])
        logger.debug('')
