from abc import ABC, abstractmethod
import logging

class OutputFormat(ABC):
    """
    Abstract Interface for different output formats.
    """
    logger = logging.getLogger('QS.Output')

    @abstractmethod
    async def serialize(self, result, error, *args, **kwargs):
        """
        Making the serialization of data.
        """

    async def __call__(self, result, error, *args, **kwargs):
        return await self.serialize(result, error, *args, **kwargs)
