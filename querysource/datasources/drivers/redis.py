from typing import Union
from datamodel import Field
from ...conf import (
    REDIS_HOST,
    REDIS_PORT,
    REDIS_URL
)
from .abstract import NoSQLDriver

class redisDriver(NoSQLDriver):
    driver: str = 'redis'
    name: str = 'Redis Server'
    port: int = Field(required=True, default=6379)
    database: Union[str, int] = Field(required=True, default=0)
    dsn_format: str = "redis://{host}:{port}/{database}"
    defaults: str = REDIS_URL

try:
    redis_default = redisDriver(
        host=REDIS_HOST,
        port=REDIS_PORT
    )
except ValueError:
    redis_default = None
