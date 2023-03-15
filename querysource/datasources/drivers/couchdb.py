from dataclasses import asdict
from datamodel import Column
from .abstract import NoSQLDriver


class couchdbDriver(NoSQLDriver):
    name: str = 'couchdb'
    host: str = Column(required=True, default='localhost')
    port: int = Column(required=True, default=5984)
    protocol: str = Column(required=False, default='http')
    database: str
    url: str = Column(required=False)
    dsn_format: str = '{protocol}://{username}:{password}@{host}:{port}/'

    def uri(self) -> str:
        params = asdict(self)
        try:
            self.url = self.dsn_format.format(**params)
            return self.url
        except (AttributeError, ValueError):
            return None

    def params(self) -> dict:
        return {
            "url": self.url
        }
