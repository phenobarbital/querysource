from dataclasses import asdict
from datamodel import Column
from ...conf import (
    ARANGODB_DRIVER,
    ARANGODB_HOST,
    ARANGODB_PORT,
    ARANGODB_USER,
    ARANGODB_PASSWORD,
    ARANGODB_DATABASE,
)
from .abstract import NoSQLDriver


class arangodbDriver(NoSQLDriver):
    """ArangoDB Driver configuration."""
    driver: str = ARANGODB_DRIVER
    port: int = Column(required=True, default=ARANGODB_PORT)
    protocol: str = Column(required=False, default='http')
    database: str = Column(required=False, default=ARANGODB_DATABASE)
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
        host=ARANGODB_HOST,
        port=ARANGODB_PORT,
        database=ARANGODB_DATABASE,
        username=ARANGODB_USER,
        password=ARANGODB_PASSWORD,
    )
except Exception:
    arangodb_default = None
