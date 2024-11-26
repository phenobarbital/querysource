from typing import Any
from abc import ABC, abstractmethod
from navconfig.logging import logging
from ...conf import DEFAULT_QUERY_TIMEOUT


class AbstractBackend(ABC):
    """
    Abstract Base class defining interface for Cache Backends.
    """
    def __init__(self, **kwargs):
        self.logger = logging.getLogger(name='QS.Cache')
        self._timeout = kwargs.getint('timeout', int(DEFAULT_QUERY_TIMEOUT))
        self._connection = None

    @abstractmethod
    async def open(self) -> "AbstractBackend":
        """
        Open the connection to the backend.
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self):
        """
        Close the connection to the backend.
        """
        pass

    async def __aenter__(self) -> "AbstractBackend":
        """
        Async enter method.
        """
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """
        Async exit method.
        """
        await self.close()

    @abstractmethod
    def exists(self, key: str):
        """
        Check if a Key exists on Cache.
        """
        pass

    in_cache = exists

    @abstractmethod
    def get(self, key: str):
        """
        Get the value for the key.
        """
        raise NotImplementedError

    @abstractmethod
    def save(self, key: str, value: Any, expiration: int = 360):
        """
        Saving Object to Backend Cache.
        """
        pass

    @abstractmethod
    def delete(self, key: str):
        """
        Delete the value for the key.
        """
        pass

    @abstractmethod
    def flush(self):
        """
        Flushing (Clear) all cache.
        """
        pass
