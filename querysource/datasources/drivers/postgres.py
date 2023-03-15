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

class postgresDriver(SQLDriver):
    driver: str = 'postgres'
    name: str = 'postgres'
    dsn_format: str = "postgres://{username}:{password}@{host}:{port}/{database}"
    port: int = Column(required=True, default=5432)
    defaults: str = asyncpg_url

postgres_default = postgresDriver(dsn=asyncpg_url, host=PG_HOST, port=PG_PORT, database=PG_DATABASE, username=PG_USER, password=PG_PWD)
