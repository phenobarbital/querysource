from typing import Union
from datamodel import Column
from querysource.conf import (
    REDIS_HOST,
    REDIS_PORT,
    REDIS_URL
)
from .abstract import NoSQLDriver

class redisDriver(NoSQLDriver):
    driver: str = 'redis'
    name: str = 'Redis Server'
    port: int = Column(required=True, default=6379)
    database: Union[str, int] = Column(required=True, default=0)
    dsn_format: str = "redis://{host}:{port}/{database}"
    defaults: str = REDIS_URL


redis_default = redisDriver(
    host=REDIS_HOST,
    port=REDIS_PORT
)
