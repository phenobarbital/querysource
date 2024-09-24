from dataclasses import InitVar
from datamodel import Column
from .abstract import SQLDriver


class odbcDriver(SQLDriver):
    driver: str = 'odbc'
    name: str = 'odbc'
    provider: str = Column(required=False, default='SQLite3')
    hostname: InitVar = ''
    dsn_format: str = "Driver={provider};Database={database}"

    def __post_init__(self, user, hostname, *args, **kwargs):
        if self.host:
            self.dsn_format = "DRIVER={provider};Database={database};server={host};uid={username};pwd={password}"
        super(odbcDriver, self).__post_init__(user, hostname, *args, **kwargs)


try:
    odbc_default = odbcDriver(provider="SQLite3", database=":memory:")
except ValueError:
    odbc_default = None
