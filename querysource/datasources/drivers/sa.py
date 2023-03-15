"""Driver for MySQL database connections, using SQLAlchemy async
"""
from datamodel import Column
from querysource.conf import (
    # MySQL Server
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_USER,
    MYSQL_PWD,
    MYSQL_DATABASE,
)
from .abstract import SQLDriver


class saDriver(SQLDriver):
    driver: str = 'sa'
    name: str = 'sa'
    provider: str = Column(required=False, default='mysql')
    dsn_format: str = "{provider}://{username}:{password}@{host}:{port}/{database}"

sa_default = saDriver(
    host=MYSQL_HOST,
    port=MYSQL_PORT,
    username=MYSQL_USER,
    password=MYSQL_PWD,
    database=MYSQL_DATABASE
)
