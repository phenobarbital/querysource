"""Driver for pg (asyncPG) database connections.
"""
from dataclasses import InitVar
from datamodel import Column
from querysource.conf import (
    # SQL Server
    SQLSERVER_DRIVER,
    SQLSERVER_HOST,
    SQLSERVER_PORT,
    SQLSERVER_USER,
    SQLSERVER_PWD,
    SQLSERVER_DATABASE,
    SQLSERVER_TDS_VERSION
)
from .abstract import SQLDriver


class sqlserverDriver(SQLDriver):
    driver: str = SQLSERVER_DRIVER
    name: str = 'MS SQL Server'
    user: str = Column(required=True)
    username: InitVar = ''
    hostname: InitVar = ''
    dsn_format: str = None
    server: str
    port: int = Column(required=True, default=1433)
    tds_version: str = Column(required=False, default='8.0')

    def __post_init__(self, username, hostname = None, **kwargs) -> None: # pylint: disable=W0613,W0221
        super(sqlserverDriver, self).__post_init__(hostname, **kwargs)
        if username is not None and self.user is None:
            self.user = username
        self.server = f"{self.host}:{self.port}"
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
            "database": self.database,
            "tds_version": self.tds_version
        }

if SQLSERVER_USER:
    sqlserver_default = sqlserverDriver(
        host=SQLSERVER_HOST,
        port=SQLSERVER_PORT,
        database=SQLSERVER_DATABASE,
        user=SQLSERVER_USER,
        password=SQLSERVER_PWD,
        tds_version=SQLSERVER_TDS_VERSION
    )
else:
    sqlserver_default = None
