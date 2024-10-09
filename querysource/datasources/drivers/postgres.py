"""Driver for pg (asyncPG) database connections.
"""
from ...conf import (
    # postgres read-only
    asyncpg_url,
    PG_HOST,
    PG_PORT,
    PG_USER,
    PG_PWD,
    PG_DATABASE
)
from .pg import pgDriver

class postgresDriver(pgDriver):
    driver: str = 'postgres'
    name: str = 'postgres'
    defaults: str = asyncpg_url

try:
    postgres_default = postgresDriver(
        dsn=asyncpg_url,
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PWD
    )
except ValueError:
    postgres_default = None
