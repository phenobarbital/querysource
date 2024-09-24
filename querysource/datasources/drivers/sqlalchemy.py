"""
Driver for Pg database connections, using SQLAlchemy
"""
from datamodel import Column
from ...conf import (
    SQLALCHEMY_DATABASE_URI
)
from .abstract import SQLDriver


class sqlalchemyDriver(SQLDriver):
    driver: str = 'sa'
    name: str = 'SQLAlchemy'
    provider: str = Column(required=False, default='postgresql')
    dsn_format: str = "{provider}://{username}:{password}@{host}:{port}/{database}"


try:
    sqlalchemy_default = sqlalchemyDriver(dsn=SQLALCHEMY_DATABASE_URI)
except ValueError:
    sqlalchemy_default = None
