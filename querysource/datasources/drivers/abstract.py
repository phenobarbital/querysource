from abc import abstractmethod
from typing import Union, Optional
from pathlib import Path
from dataclasses import asdict, InitVar
from datamodel import BaseModel, Field
from datamodel.types import JSON_TYPES
from ...conf import (
    DEFAULT_AWS_REGION,
    GOOGLE_SERVICE_FILE,
    GOOGLE_SERVICE_PATH
)

def default_properties() -> tuple:
    return ('host', 'port', 'user', 'username', 'password')

class BaseDriver(BaseModel):
    """BaseDriver.

    Description: Base class for all required datasources.
    """
    driver: str = Field(required=True, primary_key=True)
    driver_type: str = Field(
        required=True,
        default='asyncdb',
        comment="type of driver, can be asyncdb, qs or REST"
    )
    name: str = Field(required=False, comment='Datasource name, default to driver.')
    description: str = Field(comment='Datasource Description', repr=False)
    icon: str = Field(required=False, comment='Icon Path for Datasource.', repr=False)
    dsn: str = Field(default=None)
    dsn_format: str = Field(required=False, default=None, repr=False)
    user: InitVar = Field(default='')
    username: str = Field(default='')
    password: str = Field(required=False, default=None, repr=False, is_secret=True)
    auth: dict = Field(required=False, default_factory=dict)
    required_properties: Optional[tuple] = Field(repr=False, default=default_properties())

    def __post_init__(self, user, **kwargs) -> None:  # pylint: disable=W0613,W0221
        if not self.name:
            self.name = self.driver
        if user:
            self.username = user
        self.auth = {
            "username": self.username,
            "password": self.password
        }
        # set DSN (if needed)
        if self.dsn_format is not None and self.dsn is None:
            self.create_dsn()
        super(BaseDriver, self).__post_init__()

    def create_dsn(self) -> str:
        """create_dsn.

        Description: creates DSN from DSN Format.
        Returns:
            str: DSN.
        """
        params = asdict(self)
        try:
            self.dsn = self.dsn_format.format(**params)
            return self.dsn
        except (AttributeError, ValueError):
            return None

    @abstractmethod
    def params(self) -> dict:
        """params.

        Returns:
            dict: params required for AsyncDB.
        """
        return {}

    def get_credentials(self) -> dict:
        """get_credentials.

        Description: Returns credentials for Datasource.
        Returns:
            dict: credentials.
        """
        return self.params()

    def get_parameters(self) -> dict:
        return {}

    @classmethod
    def properties(cls) -> dict:
        """properties.

        Description: Returns fields related to Drivers Supported.
        Returns:
            dict: all required fields for Supported Drivers.
        """

        fields = {}
        for field in cls.required_properties:
            # because tuple is ordered:
            try:
                f = cls.column(cls, field)
            except KeyError:
                continue  # Field Missing on Driver:
            secret = False
            if 'is_secret' in f.metadata:
                secret = f.metadata["is_secret"]
            title = field
            if 'title' in f.metadata:
                title = f.metadata['title']
            required = False
            if 'required' in f.metadata:
                required = f.metadata['required']
            try:
                _type = JSON_TYPES[f.type]
            except KeyError:
                _type = str(f.type)
            f = {
                "name": field,
                "title": title,
                "required": required,
                "type": _type,
                "is_secret": secret
            }
            value = getattr(cls, field)
            default = hasattr(f, 'default')
            if not value and default:
                value = f.default
            if value:
                f["value"] = value
            fields[field] = f
        return {
            "driver": cls.driver,
            "name": cls.name,
            "icon": cls.icon,
            "dsn_format": cls.dsn_format,
            "fields": fields
        }

### SQL-based or SQL-like Drivers
def default_sql_properties() -> tuple:
    return ('host', 'port', 'user', 'username', 'password', 'database', 'dsn')


class SQLDriver(BaseDriver):
    """
    Description: Base Class for all SQL Drivers.
    """
    hostname: InitVar = ''
    host: str = Field(required=False, default='localhost')
    port: int
    username: str = Field(required=False, default=None, repr=True)
    password: str = Field(required=False, default=None, repr=False, is_secret=True)
    database: str
    required_properties: Optional[tuple] = Field(
        repr=False,
        default=default_sql_properties()
    )

    def __post_init__(
        self,
        user,
        hostname: str = None,
        *args,
        **kwargs
    ):  # pylint: disable=W0613,W0221
        if hostname:
            self.host = hostname
        super(SQLDriver, self).__post_init__(user, *args, **kwargs)

    def params(self) -> dict:
        """params

        Returns:
            dict: params required for AsyncDB.
        """
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password
        }

    def get_parameters(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
        }


class NoSQLDriver(BaseDriver):
    host: str = Field(required=False, default='localhost')
    port: Union[int, str] = Field(required=False)

    def params(self) -> dict:
        """params

        Returns:
            dict: params required for AsyncDB.
        """
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password
        }

    def get_parameters(self) -> dict:
        return {
            "host": self.host,
            "port": self.port
        }


### Cloud-based Providers
def cloud_properties() -> tuple:
    return (
        'url',
        'url_schema',
        'host',
        'port',
        'user',
        'username',
        'password',
        'protocol',
        'access_token'
    )
class CloudDriver(BaseDriver):
    hostname: InitVar = ''
    driver_type: str = Field(required=False, default='external')
    host: str = Field(required=False, default='localhost')
    port: Union[int, str] = Field(required=False)
    base_url: str
    url: str = Field(required=False)
    url_schema: str = '{protocol}://{username}:{password}@{host}:{port}/'
    access_token: str = Field(required=False)
    protocol: str = Field(required=False, default='http')
    required_properties: Optional[tuple] = Field(repr=False, default=cloud_properties())

    def __post_init__(self, user, hostname, *args, **kwargs):  # pylint: disable=W0613,W0221
        if hostname:
            self.host = hostname
        if not self.url:
            self.url = self.uri()
        super(CloudDriver, self).__post_init__(user, *args, **kwargs)

    def uri(self) -> str:
        params = asdict(self)
        try:
            return self.url_schema.format(**params)
        except (AttributeError, ValueError):
            return None

    def get_parameters(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "url": self.url,
        }


### Amazon services:
def aws_properties() -> tuple:
    return ('url', 'region', 'access_key', 'secret_key', 'use_credentials')

class AWSDriver(CloudDriver):
    """AWSDriver
    Abstract base class for all Amazon-related services.
    """
    region: str = Field(required=True, default=DEFAULT_AWS_REGION)
    access_key: str = Field(required=False)
    secret_key: str = Field(required=False)
    use_credentials: bool = Field(required=False, default=True)
    required_properties: Optional[tuple] = Field(repr=False, default=aws_properties())


### Google Services:
def google_properties() -> tuple:
    return ('url', 'json_key', 'service_path')


class GoogleDriver(CloudDriver):
    """GoogleDriver
    Abstract base class for all Google-related services.
    """
    json_key: Union[str, Path] = Field(required=False, default=GOOGLE_SERVICE_FILE)
    service_path: Union[str, Path] = Field(required=False, default=GOOGLE_SERVICE_PATH)
    required_properties: Optional[tuple] = Field(
        repr=False, default=google_properties()
    )

    def credentials(self) -> str:
        cred = self.service_path.joinpath(self.json_key)
        if cred.exists():
            return str(cred)
        else:
            return None
