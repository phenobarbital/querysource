from dataclasses import InitVar
from datamodel import Column
from querysource.conf import (
    # influxdb
    INFLUX_DRIVER,
    INFLUX_HOST,
    INFLUX_PORT,
    INFLUX_USER,
    INFLUX_PWD,
    INFLUX_DATABASE,
    INFLUX_ORG,
    INFLUX_TOKEN
)
from .abstract import NoSQLDriver


class influxDriver(NoSQLDriver):
    driver: str = INFLUX_DRIVER
    host: str = Column(required=False, default='127.0.0.1')
    port: int = Column(required=True, default=8086)
    user: str
    database: InitVar = ''
    org: str
    bucket: str = Column(required=False)
    token: str = Column(required=True)

    def __post_init__(self, user, database: str = None, *args, **kwargs):  # pylint: disable=W0221
        if not self.bucket:
            self.bucket = database
        super(influxDriver, self).__post_init__(user, *args, **kwargs)

    def params(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "org": self.org,
            "bucket": self.bucket,
            "token": self.token
        }

influx_default = influxDriver(
    host=INFLUX_HOST,
    port=INFLUX_PORT,
    user=INFLUX_USER,
    password=INFLUX_PWD,
    bucket=INFLUX_DATABASE,
    org=INFLUX_ORG,
    token=INFLUX_TOKEN
)
