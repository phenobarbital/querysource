from datamodel import Column
from querysource.conf import (
    rt_driver,
    rt_host,
    rt_port,
    rt_database,
    rt_user,
    rt_password,
)
from .abstract import NoSQLDriver

class rethinkDriver(NoSQLDriver):
    driver: str = rt_driver
    port: int = Column(required=True, default=28015)
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

rethink_default = rethinkDriver(
    host=rt_host,
    port=rt_port,
    database=rt_database,
    username=rt_user,
    password=rt_password
)
