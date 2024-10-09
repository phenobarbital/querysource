from dataclasses import asdict
from datamodel import Column
from .abstract import NoSQLDriver


class arangodbDriver(NoSQLDriver):
    driver: str = 'arangodb'
    port: int = Column(required=True, default=8529)
    protocol: str = Column(required=False, default='http')
    database: str = Column(required=False, default='_system')
    url: str = Column(required=False)
    dsn_format: str = '{protocol}://{host}:{port}/'

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
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "password": self.password,
        }


try:
    arangodb_default = arangodbDriver(
        host='localhost',
        port=8529,
        username='root',
        password='password'
    )
except Exception:
    arangodb_default = None
