from dataclasses import asdict
from datamodel import Column
from .abstract import NoSQLDriver
from ...conf import (
    CLICKHOUSE_DRIVER,
    CLICKHOUSE_HOST,
    CLICKHOUSE_PORT,
    CLICKHOUSE_USER,
    CLICKHOUSE_PASSWORD,
    CLICKHOUSE_DATABASE,
    CLICKHOUSE_SECURE,
    CLICKHOUSE_CLIENT_NAME,
)


class clickhouseDriver(NoSQLDriver):
    driver: str = CLICKHOUSE_DRIVER
    name: str = CLICKHOUSE_CLIENT_NAME
    host: str = Column(required=True, default='127.0.0.1')
    port: int = Column(required=False, default=9000)
    user: str = Column(required=True)
    password: str = Column(required=True)
    database: str = Column(required=False, default='default')
    secure: bool = Column(required=False, default=False)
    protocol: str = Column(required=False, default='http')
    url: str = Column(required=False)
    dsn_format: str = "{protocol}://{host}:{port}/"
    cert_path: str = Column(required=False)

    def uri(self) -> str:
        params = asdict(self)
        try:
            self.url = self.dsn_format.format(**params)
            return self.url
        except (AttributeError, ValueError):
            return None

    def params(self) -> dict:
        if self.driver == 'aioch':
            self.port = 8123
            return {
                "url": self.uri(),
                "user": self.user,
                "password": self.password,
                "database": self.database,
            }
        return {
            "host": self.host,
            "port": 9000,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "client_name": self.name,
            "secure": self.secure
        }


try:
    clickhouse_default = clickhouseDriver(
        driver=CLICKHOUSE_DRIVER,
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_PORT,
        user=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
        database=CLICKHOUSE_DATABASE,
        secure=CLICKHOUSE_SECURE
    )
except (ValueError, TypeError):
    clickhouse_default = None
