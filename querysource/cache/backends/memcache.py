from typing import Optional, Any
import asyncio
import aiomcache
from .abstract import AbstractBackend
from ...conf import QUERYSET_MEMCACHE
from ...exceptions import (
    CacheException
)

class MemcacheBackend(AbstractBackend):
    """
    Memcache Cache Backend.

    Use Memcached as a Caching Mechanism for Queries.
    """

    def __init__(self, **kwargs):
        self._dsn: str = kwargs.get('dsn', QUERYSET_MEMCACHE)
        self._host, self._port = self._parse_dsn(self._dsn)
        self._timeout: int = kwargs.get('timeout', 10)
        super().__init__(**kwargs)

    @staticmethod
    def _parse_dsn(dsn: str):
        try:
            host, port = dsn.split(":")
            return host, int(port)
        except ValueError as err:
            raise CacheException(f"Invalid DSN format: {dsn}") from err

    async def open(self):
        try:
            self._connection = aiomcache.Client(
                self._host,
                self._port,
            )
        except Exception as ex:  # pylint: disable=W0703
            self._logger.error(ex)
            raise CacheException(
                f"Unable to connect to Memcached: {ex}"
            )

    async def close(self):
        try:
            await self._connection.close()
        except Exception as ex:  # pylint: disable=W0703
            self._logger.warning(ex)

    async def ping(self, msg: str = None):
        try:
            if msg:
                await self._connection.set("ping", msg.encode())
                return await self._connection.get("ping") == msg.encode()
            return True
        except Exception as ex:
            self._logger.error(f"Ping failed: {ex}")
            return False

    async def exists(self, key: str, keys: Optional[list] = None):
        if keys:
            # Memcached does not support batch existence checks.
            raise NotImplementedError("Batch existence check is not supported in Memcached.")
        try:
            return await self._connection.get(key.encode()) is not None
        except Exception as ex:
            self._logger.warning(f"Exists check failed for key {key}: {ex}")
            return False

    in_cache = exists

    async def get(self, key: str) -> Any:
        try:
            value = await self._connection.get(key.encode())
            return value.decode() if value else None
        except asyncio.TimeoutError:
            try:
                await self.open()
                value = await self._connection.get(key.encode())
                return value.decode() if value else None
            except Exception as exc:
                self._logger.exception(f"Failure on Memcached Cache: {exc}")
                return None
        except Exception:
            self._logger.warning(f"Key not found in Cache: {key}")
            return None

    from_cache = get

    async def save(self, key: str, data: Any, expiration: Optional[int] = None):
        if expiration is None:
            expiration = self._timeout
        try:
            await self._connection.set(
                key.encode(),
                str(data).encode(),
                exptime=expiration
            )
            self._logger.debug(f"Successfully Cached: {key}")
        except asyncio.TimeoutError as err:
            self._logger.error(f"Memcached timeout: {err}")
            raise
        except Exception as err:
            raise CacheException(
                f"Error on Memcached cache: {err}"
            ) from err

    async def delete(self, key: str):
        try:
            await self._connection.delete(key.encode())
        except Exception as ex:
            self._logger.warning(f"Delete failed for key {key}: {ex}")

    async def flush(self):
        try:
            await self._connection.flush_all()
        except Exception as ex:
            self._logger.error(f"Flush failed: {ex}")
            raise CacheException(f"Error flushing Memcached cache: {ex}")
