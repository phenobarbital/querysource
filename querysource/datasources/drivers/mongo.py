"""MongoDB Driver Definition.
"""
from datamodel import Column
from ...conf import (
    MONGO_DRIVER,
    MONGO_HOST,
    MONGO_PORT,
    MONGO_DATABASE,
    MONGO_USER,
    MONGO_PASSWORD,
)
from .abstract import NoSQLDriver

class mongoDriver(NoSQLDriver):
    driver: str = MONGO_DRIVER
    port: int = Column(required=True, default=27017)
    database: str = Column(required=False)
    dsn_format: str = "mongodb://{host}:{port}"

    def params(self) -> dict:
        """params

        Returns:
            dict: params required for AsyncDB.
        """
        if self.user:
            return {
                "host": self.host,
                "port": self.port,
                "username": self.username,
                "password": self.password,
                "db": self.database,
            }
        else:
            return {
                "host": self.host,
                "port": self.port,
                "db": self.database
            }

try:
    mongo_default = mongoDriver(
        driver=MONGO_DRIVER,
        host=MONGO_HOST,
        port=MONGO_PORT,
        database=MONGO_DATABASE,
        username=MONGO_USER,
        password=MONGO_PASSWORD
    )
except (TypeError, ValueError):
    mongo_default = None
