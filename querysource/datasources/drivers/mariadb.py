"""Driver for pg (asyncPG) database connections.
"""
from dataclasses import InitVar
from datamodel import Column
from ...conf import (
    # MySQL Server
    MYSQL_DRIVER,
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_USER,
    MYSQL_PWD,
    MYSQL_DATABASE,
)
from .abstract import SQLDriver


class mariadbDriver(SQLDriver):
    driver: str = MYSQL_DRIVER
    name: str = MYSQL_DRIVER
    user: str
    username: InitVar = ''
    hostname: InitVar = ''
    dsn_format: str = "mysql://{user}:{password}@{host}:{port}/{database}"
    port: int = Column(required=True, default=3306)

    def __post_init__(
        self,
        username,
        hostname: str = None,
        **kwargs
    ) -> None:  # pylint: disable=W0613,W0221 # noqa
        super(mariadbDriver, self).__post_init__(hostname, **kwargs)
        if username is not None and self.user is None:
            self.user = username
        self.auth = {
            "user": self.user,
            "password": self.password
        }

    def params(self) -> dict:
        """params

        Returns:
            dict: params required for AsyncDB.
        """
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database
        }

try:
    mariadb_default = mariadbDriver(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        database=MYSQL_DATABASE,
        user=MYSQL_USER,
        password=MYSQL_PWD
    )
except ValueError:
    mariadb_default = None
