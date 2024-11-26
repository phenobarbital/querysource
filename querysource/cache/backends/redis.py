from typing import Optional, Any
import asyncio
from redis import asyncio as aioredis
from .abstract import AbstractBackend
from ...conf import QUERYSET_REDIS
from ...exceptions import (
    CacheException
)

class RedisBackend(AbstractBackend):
    """
    Redis Cache Backend.

    Use Redis as a Caching Mechanism for Queries.
    """

    def __init__(self, **kwargs):
        self._dsn: str = kwargs.get('dsn', QUERYSET_REDIS)
        super().__init__(**kwargs)

    async def open(self):
        try:
            self._connection = await aioredis.from_url(
                QUERYSET_REDIS,
                encoding='utf-8',
                decode_responses=True,
            )
        except Exception as ex:  # pylint: disable=W0703
            self._logger.error(ex)
            raise CacheException(
                f"Unable to connect to Redis: {ex}"
            )

    async def close(self):
        try:
            await self._connection.close()
        except Exception as ex:  # pylint: disable=W0703
            self._logger.warning(ex)

    async def ping(self, msg: str = None):
        if msg is not None:
            await self._connection.echo(msg)
        return await self._connection.ping()

    async def exists(self, key: str, keys: Optional[list] = None):
        return await self._connection.exists(key, *keys)

    in_cache = exists

    async def get(self, key: str) -> Any:
        try:
            return await self._connection.get(key)
        except asyncio.TimeoutError:
            # trying to reconect:
            try:
                await self.open()
                return await self._connection.get(key)
            except Exception as exc:
                self.logger.exception(
                    f"Failure on REDIS Cache: {exc}"
                )
                return False
        except Exception:
            self.logger.warning(
                f"Key not found on Cache: {key}"
            )
            return None

    from_cache = get

    async def save(self, key: str, data: Any, expiration: Optional[int] = None):
        if expiration is None:
            expiration = self._timeout
        try:
            await self._connection.setex(
                key,
                data,
                expiration
            )
            self.logger.debug(
                f"Successfully Cached: {key}"
            )
        except asyncio.TimeoutError as err:
            self.logger.error(
                f"Redis timeout: {err}"
            )
            raise
        except Exception as err:
            raise CacheException(
                f'Error on Redis cache: {err}'
            ) from err

    async def delete(self, key: str):
        await self._connection.delete(key)

    async def flush(self):
        await self._connection.flushdb()
