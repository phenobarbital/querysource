from typing import Union
from datamodel import Field
from navconfig import BASE_DIR
from pathlib import Path
from .mongo import mongoDriver
from ...conf import (
    MONGO_DRIVER,
    DOCUMENTDB_HOSTNAME,
    DOCUMENTDB_PORT,
    DOCUMENTDB_DATABASE,
    DOCUMENTDB_USERNAME,
    DOCUMENTDB_PASSWORD,
    DOCUMENTDB_TLSFILE,
    DOCUMENTDB_USE_SSL,
)

class documentdbDriver(mongoDriver):
    driver: str = MONGO_DRIVER
    dbtype: str = "documentdb"
    ssl: bool = Field(required=False, default=DOCUMENTDB_USE_SSL)
    tlsCAFile: Union[str, Path] = Field(required=False, default=DOCUMENTDB_TLSFILE)

    def params(self):
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "db": self.database,
            "dbtype": self.dbtype,
            "ssl": self.ssl,
            "tlsCAFile": self.tlsCAFile
        }

try:
    documentdb_default = documentdbDriver(
        host=DOCUMENTDB_HOSTNAME,
        port=DOCUMENTDB_PORT,
        database=DOCUMENTDB_DATABASE,
        username=DOCUMENTDB_USERNAME,
        password=DOCUMENTDB_PASSWORD,
        ssl=DOCUMENTDB_USE_SSL,
        tlsCAFile=DOCUMENTDB_TLSFILE
    )
except (TypeError, ValueError):
    documentdb_default = None
