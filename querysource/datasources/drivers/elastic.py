"""Elasticsearch Driver.

Datasource driver for Elasticsearch using asyncdb.
"""
from datamodel import Column
from ...conf import (
    ELASTIC_DRIVER,
    ELASTIC_HOST,
    ELASTIC_PORT,
    ELASTIC_USER,
    ELASTIC_PASSWORD,
    ELASTIC_DATABASE,
    ELASTIC_PROTOCOL,
    ELASTIC_USE_SSL,
)
from .abstract import NoSQLDriver


class elasticDriver(NoSQLDriver):
    """Elasticsearch datasource driver."""
    driver: str = ELASTIC_DRIVER
    port: int = Column(required=True, default=ELASTIC_PORT)
    database: str = Column(required=False)
    protocol: str = Column(required=False, default=ELASTIC_PROTOCOL)
    use_ssl: bool = Column(required=False, default=ELASTIC_USE_SSL)

    def params(self) -> dict:
        """Return params required for asyncdb elastic driver."""
        p = {
            "host": self.host,
            "port": self.port,
            "db": self.database,
            "protocol": self.protocol,
        }
        if self.username:
            p["user"] = self.username
            p["password"] = self.password
        if self.use_ssl:
            p["use_ssl"] = True
            p["verify_certs"] = False
        return p


try:
    elastic_default = elasticDriver(
        host=ELASTIC_HOST,
        port=ELASTIC_PORT,
        database=ELASTIC_DATABASE,
        username=ELASTIC_USER,
        password=ELASTIC_PASSWORD,
        protocol=ELASTIC_PROTOCOL,
        use_ssl=ELASTIC_USE_SSL,
    )
except (TypeError, ValueError):
    elastic_default = None
