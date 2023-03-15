from datamodel import Column
from .abstract import NoSQLDriver


class couchbaseDriver(NoSQLDriver):
    name: str = 'couchbase'
    host: str = Column(required=True, default='127.0.0.1')
    port: int = Column(required=True, default=8095)
    protocol: str = Column(required=False, default='http')
    bucket: str
    dsn_format: str = 'couchbase://{host}:{port}/'
    cert_path: str = Column(required=False)

    def __post_init__(self, user, **kwargs) -> None: # pylint: disable=W0613,W0221
        super(couchbaseDriver, self).__post_init__(user, **kwargs)
        self.auth = {
            "username": self.username,
            "password": self.password,
            "cert_path": self.cert_path
        }
