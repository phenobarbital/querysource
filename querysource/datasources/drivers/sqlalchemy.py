"""Driver for MySQL database connections, using SQLAlchemy async
"""
from datamodel import Column
from querysource.conf import (
    SQLALCHEMY_DATABASE_URI
)
from .abstract import SQLDriver


class sqlalchemyDriver(SQLDriver):
    driver: str = 'sqlalchemy'
    name: str = 'sa'
    provider: str = Column(required=False, default='mysql')
    dsn_format: str = "{provider}://{username}:{password}@{host}:{port}/{database}"

sqlalchemy_default = sqlalchemyDriver(dsn=SQLALCHEMY_DATABASE_URI)
