from datamodel import Field
from .abstract import SQLDriver

class sqliteDriver(SQLDriver):
    driver: str = 'sqlite'
    username: str = Field(required=False, default=None, repr=False)
    password: str = Field(required=False, default=None, repr=False, is_secret=True)
    dsn_format: str = "{database}"

try:
    sqlite_default = sqliteDriver(database=":memory:")
except ValueError:
    sqlite_default = None
