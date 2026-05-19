"""
AbstractDestination.

Base class for all MultiQuery destination components.
"""
import re
from abc import ABC, abstractmethod
from typing import Union
import pandas as pd
from navconfig.logging import logging


# Pattern to detect navconfig variable names (ALL_CAPS_SNAKE_CASE)
_NAVCONFIG_PATTERN = re.compile(r'^[A-Z][A-Z0-9_]+$')


class AbstractDestination(ABC):
    """
    AbstractDestination.

    Base class for all MultiQuery destination components.
    Subclasses must implement :meth:`run` to write data to their target backend
    and return the original data (pass-through) for pipeline chaining.
    """

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        self.data = data
        self.logger = logging.getLogger(
            f'QS.Output.{self.__class__.__name__}'
        )

    def resolve_credentials(self, credentials: dict) -> dict:
        """
        Resolve navconfig variable names to actual values.

        For each value in *credentials*, if the value is a string matching
        the ALL_CAPS_SNAKE_CASE pattern it is treated as a navconfig variable
        name and looked up via ``navconfig.config.get()``.  If the lookup
        returns ``None`` the original (unresolved) value is kept so that
        callers can see what was configured.

        :param credentials: Raw credential dictionary (may contain variable names).
        :returns: Resolved credential dictionary.
        """
        from navconfig import config  # deferred import to avoid circular deps

        resolved: dict = {}
        for key, value in credentials.items():
            if isinstance(value, str) and _NAVCONFIG_PATTERN.match(value):
                resolved_val = config.get(value)
                resolved[key] = resolved_val if resolved_val is not None else value
            else:
                resolved[key] = value
        return resolved

    @abstractmethod
    async def run(self) -> Union[dict, pd.DataFrame]:
        """
        Write data to the destination backend.

        Implementations must write :attr:`data` to their target backend and
        **return the original data unchanged** so that subsequent destinations
        in a multi-output pipeline each receive the full result set.

        :returns: The original :attr:`data` (pass-through).
        """

    async def close(self) -> None:
        """
        Clean up any resources held by this destination (optional).

        Override in subclasses that maintain persistent connections or
        temporary files.
        """
