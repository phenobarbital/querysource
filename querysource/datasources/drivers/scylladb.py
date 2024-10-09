from datamodel import Column
from ...conf import (
    # ScyllaDB
    SCYLLA_DRIVER,
    SCYLLA_HOST,
    SCYLLA_PORT,
    SCYLLA_USER,
    SCYLLA_PWD,
    SCYLLA_DATABASE
)
from .abstract import SQLDriver


class scylladbDriver(SQLDriver):
    driver: str = SCYLLA_DRIVER
    name: str = SCYLLA_DRIVER
    dsn_format: str = None
    port: int = Column(required=True, default=9042)


try:
    scylladb_default = scylladbDriver(
        host=SCYLLA_HOST,
        port=SCYLLA_PORT,
        database=SCYLLA_DATABASE,
        username=SCYLLA_USER,
        password=SCYLLA_PWD
    )
except (TypeError, ValueError):
    scylladb_default = None
