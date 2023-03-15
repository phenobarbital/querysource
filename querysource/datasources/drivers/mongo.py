"""MongoDB Driver Definition.
"""
from datamodel import Column
from querysource.conf import (
    mongo_driver,
    mongo_host,
    mongo_port,
    mongo_database,
    mongo_user,
    mongo_password,
)
from .abstract import NoSQLDriver

class mongoDriver(NoSQLDriver):
    driver: str = mongo_driver
    port: int = Column(required=True, default=27017)
    database: str = Column(required=False)

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

mongo_default = mongoDriver(
    host=mongo_host,
    port=mongo_port,
    database=mongo_database,
    username=mongo_user,
    password=mongo_password
)
