from typing import Union
from datamodel import Field
from ...conf import (
    MEMCACHE_DRIVER,
    MEMCACHE_HOST,
    MEMCACHE_PORT,
    MEMCACHE_SERVICE
)
from .abstract import NoSQLDriver


class memcacheDriver(NoSQLDriver):
    driver: str = MEMCACHE_DRIVER
    name: str = 'Memcached Server'
    port: int = Field(required=True, default=MEMCACHE_PORT)
    database: Union[str, int] = Field(required=True, default=0)
    dsn_format: str = None
    defaults: str = MEMCACHE_SERVICE


try:
    redis_default = memcacheDriver(
        host=MEMCACHE_HOST,
        port=MEMCACHE_PORT
    )
except ValueError:
    redis_default = None
