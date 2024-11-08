"""Driver for pg (asyncPG) database connections.
"""
from dataclasses import InitVar
from datamodel import Column
from ...conf import (
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
    user: str
    username: InitVar
    hostname: InitVar
    dsn_format: str = Column(
        default="postgres://{user}:{password}@{host}:{port}/{database}",
        repr=False
    )
    port: int = Column(required=True, default=5432)

    def __post_init__(
        self,
        username: str = None,
        hostname: str = None,
        *args,
        **kwargs
    ):  # pylint: disable=W0613,W0221
        if hostname:
            self.host = hostname
        if username:
            self.user = username
        super().__post_init__(username, *args, **kwargs)

    def params(self) -> dict:
        """params

        Returns:
            dict: params required for AsyncDB.
        """
        return {
            "host": self.host,
            "port": self.port,
            "username": self.user,
            "password": self.password,
            "database": self.database
        }

try:
    pg_default = pgDriver(
        dsn=asyncpg_url,
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PWD
    )
except ValueError:
    pg_default = None
