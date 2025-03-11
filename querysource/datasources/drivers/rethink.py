from datamodel import Column
from ...conf import (
    RT_DRIVER,
    RT_HOST,
    RT_PORT,
    RT_DATABASE,
    RT_USER,
    RT_PASSWORD,
)
from .abstract import NoSQLDriver

class rethinkDriver(NoSQLDriver):
    driver: str = RT_DRIVER
    port: int = Column(required=True, default=RT_PORT)
    database: str = Column(required=False)

    def params(self) -> dict:
        """params

        Returns:
            dict: params required for AsyncDB.
        """
        if self.username:
            return {
                "host": self.host,
                "port": self.port,
                "user": self.username,
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
    rethink_default = rethinkDriver(
        host=RT_HOST,
        port=RT_PORT,
        database=RT_DATABASE,
        username=RT_USER,
        password=RT_PASSWORD
    )
except Exception as ex:
    print('RethinkDB > ', ex)
    rethink_default = None
