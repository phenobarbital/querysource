from datamodel import Column
from querysource.conf import (
    # cassandra
    CASSANDRA_DRIVER,
    CASSANDRA_HOST,
    CASSANDRA_PORT,
    CASSANDRA_USER,
    CASSANDRA_PWD,
    CASSANDRA_DATABASE
)
from .abstract import SQLDriver


class cassandraDriver(SQLDriver):
    driver: str = CASSANDRA_DRIVER
    name: str = CASSANDRA_DRIVER
    dsn_format: str = None
    port: int = Column(required=True, default=9042)

cassandra_default = cassandraDriver(
    host=CASSANDRA_HOST,
    port=CASSANDRA_PORT,
    database=CASSANDRA_DATABASE,
    username=CASSANDRA_USER,
    password=CASSANDRA_PWD
)
