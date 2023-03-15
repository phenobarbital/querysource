"""Driver for pg (asyncPG) database connections.
"""
from datamodel import Column
from querysource.conf import (
    # postgres read-only
    asyncpg_url,
    PG_HOST,
    PG_PORT,
    PG_USER,
    PG_PWD,
    PG_DATABASE
)
from .abstract import SQLDriver


class pgDriver(SQLDriver):
    driver: str = 'pg'
    name: str = 'PostgreSQL (using asyncpg)'
    dsn_format: str = Column(default="postgres://{username}:{password}@{host}:{port}/{database}", repr=False)
    port: int = Column(required=True, default=5432)

pg_default = pgDriver(dsn=asyncpg_url, host=PG_HOST, port=PG_PORT, database=PG_DATABASE, username=PG_USER, password=PG_PWD)
