from dataclasses import asdict
from datamodel import Column
from .abstract import NoSQLDriver


class clickhouseDriver(NoSQLDriver):
    name: str = 'clickhouse'
    host: str = Column(required=True, default='127.0.0.1')
    port: int = Column(required=True, default=8123)
    protocol: str = Column(required=False, default='http')
    database: str
    url: str = Column(required=False)
    dsn_format: str = 'couchbase://{host}:{port}/'
    cert_path: str = Column(required=False)

    def uri(self) -> str:
        params = asdict(self)
        try:
            self.url = self.dsn_format.format(**params)
            return self.url
        except (AttributeError, ValueError):
            return None

    def params(self) -> dict:
        return {
            "url": self.uri(),
            "user": self.user,
            "password": self.password,
            "database": self.bucket,
        }
