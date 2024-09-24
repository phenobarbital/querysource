"""Driver for pg database connections, using SQLAlchemy async
"""
from datamodel import Column
from ...conf import (
    # postgres read-only
    PG_HOST,
    PG_PORT,
    PG_USER,
    PG_PWD,
    PG_DATABASE
)
from .abstract import SQLDriver


class saDriver(SQLDriver):
    driver: str = 'sa'
    name: str = 'sa'
    provider: str = Column(required=False, default='postgresql+asyncpg')
    dsn_format: str = "{provider}://{username}:{password}@{host}:{port}/{database}"

try:
    sa_default = saDriver(
        host=PG_HOST,
        port=PG_PORT,
        username=PG_USER,
        password=PG_PWD,
        database=PG_DATABASE
    )
except (TypeError, ValueError):
    sa_default = None
