"""JDBC Configuration Driver for QuerySource.
"""
from typing import Union, Optional
from pathlib import Path
from dataclasses import InitVar
from datamodel import Field
from datamodel.exceptions import ValidationError
from ...conf import (
    JDBC_DRIVER,
    JDBC_HOST,
    JDBC_PORT,
    JDBC_USER,
    JDBC_PWD,
    JDBC_DATABASE,
    JDBC_JAR,
    JDBC_CLASSPATH,
)
from .abstract import SQLDriver

def jdbc_properties() -> tuple:
    return ('host', 'port', 'user', 'password', 'database', 'dsn', 'jar', 'classpath')

class jdbcDriver(SQLDriver):
    driver: str = 'jdbc'
    name: str
    provider: str = Field(required=False, default='oracle')
    hostname: InitVar = ''
    username: InitVar = ''
    user: str = Field(required=False, default=None, repr=True)
    password: str = Field(required=False, default=None, repr=False)
    dsn_format: str = None
    jar: Union[list, str] = Field(Required=True)
    classpath: Path = Field(Required=False)
    required_properties: Optional[Union[list, tuple]] = Field(
        repr=False, default=jdbc_properties()
    )

    def __post_init__(self, username, hostname, *args, **kwargs):
        if isinstance(self.jar, str):
            self.jar = [Path(self.jar)]
        if not self.classpath:
            self.classpath = JDBC_CLASSPATH
        if self.jar and not self.classpath:
            self.classpath = self.jar[0].dirname
        super(jdbcDriver, self).__post_init__(username, hostname, *args, **kwargs)

    def params(self) -> dict:
        return {
            "driver": self.provider,
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "jar": self.jar,
            "classpath": self.classpath
        }


try:
    try:
        jdbc_default = jdbcDriver(
            provider=JDBC_DRIVER,
            database=JDBC_DATABASE,
            user=JDBC_USER,
            password=JDBC_PWD,
            host=JDBC_HOST,
            port=JDBC_PORT,
            jar=JDBC_JAR,
            classpath=JDBC_CLASSPATH
        )
    except ValidationError as exc:
        jdbc_default = None
        print('JDBC >', exc.payload)
except ValueError:
    jdbc_default = None
