from typing import Union
from datamodel import Column
from ...conf import (
    REDIS_HOST,
    REDIS_PORT,
    REDIS_URL
)
from .abstract import NoSQLDriver

class hazelDriver(NoSQLDriver):
    driver: str = 'hazel'
    name: str = 'Hazelcast Server'
    port: int = Column(required=True, default=6379)
    database: Union[str, int] = Column(required=True, default=0)
    dsn_format: str = None
    defaults: str = REDIS_URL


hazel_default = hazelDriver(
    host=REDIS_HOST,
    port=REDIS_PORT
)
