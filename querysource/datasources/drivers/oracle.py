"""Driver for Oracle database connections.
"""
from typing import Optional
from dataclasses import InitVar
from datamodel import Column
from querysource.conf import (
    # ORacle Server
    ORACLE_DRIVER,
    ORACLE_HOST,
    ORACLE_PORT,
    ORACLE_USER,
    ORACLE_PWD,
    ORACLE_DATABASE,
    ORACLE_CLIENT
)
from .abstract import SQLDriver

def oracle_properties() -> tuple:
    return ('host', 'port', 'user', 'password', 'database', 'dsn', 'client' )

class oracleDriver(SQLDriver):
    driver: str = ORACLE_DRIVER
    name: str = ORACLE_DRIVER
    port: int = Column(required=True, default=1521)
    user: str = Column(required=True)
    username: InitVar = ''
    dsn_format: str = "{host}:{port}/{database}"
    client: str = Column(required=True)
    required_properties: Optional[list] = Column(repr=False, default=oracle_properties())

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
            "oracle_client": self.client
        }

if ORACLE_CLIENT:
    oracle_default = oracleDriver(
        host=ORACLE_HOST,
        port=ORACLE_PORT,
        database=ORACLE_DATABASE,
        user=ORACLE_USER,
        password=ORACLE_PWD,
        client=ORACLE_CLIENT
    )
else:
    oracle_default = None
