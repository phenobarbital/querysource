"""""
Connections Manager.
"""
from typing import Any
import asyncio
from importlib import import_module
from datetime import datetime
from datamodel import BaseModel
from datamodel.exceptions import ValidationError
from asyncdb import AsyncDB
from asyncdb.exceptions import (
    ProviderError,
    DriverError,
    NoDataFound
)
from navconfig.logging import logging
from ..providers import BaseProvider
from ..exceptions import (
    QueryException,
    ConfigError,
    QueryError,
    SlugNotFound
)
from ..datasources.drivers import SUPPORTED, DataDriver
from ..conf import (
    DB_MAX_WORKERS,
    DB_KEEPALIVE_IDLE,
    DB_IDLE_IN_TRANSACTION_TIMEOUT,
    DB_SESSION_TIMEOUT,
    POSTGRES_TIMEOUT,
    asyncpg_url,
    POSTGRES_SSL,
    POSTGRES_SSL_CA,
    POSTGRES_SSL_CERT,
    POSTGRES_SSL_KEY
)
from ..models import QueryModel


DATASOURCES: dict = {}
PROVIDERS: dict = {}
SLUG_CACHE: dict = {}
EXTERNAL_PROVIDERS = ('http', 'rest', )


class Connection:
    """
    Interface for managing database and services connections.
    """
    pgargs: dict = {
        "min_size": 2,
        "server_settings": {
            "application_name": "QS.Master",
            "client_min_messages": "notice",
            "max_parallel_workers": f"{DB_MAX_WORKERS}",
            "jit": "on",
            "effective_cache_size": "2147483647",
            "tcp_keepalives_idle": f"{DB_KEEPALIVE_IDLE}",
            "idle_in_transaction_session_timeout": f"{DB_IDLE_IN_TRANSACTION_TIMEOUT}",
            "idle_session_timeout": f"{DB_SESSION_TIMEOUT}"
        }
    }

    def __init__(self, **kwargs):
        self._connection = None
        self._dsmodule = None
        self._loop: asyncio.AbstractEventLoop = kwargs.get(
            'loop',
            asyncio.get_event_loop()
        )
        self._dsn: str = kwargs.get('dsn', asyncpg_url)
        self._default_driver: str = kwargs.get('driver', 'pg')
        self.logger = logging.getLogger(name='QS.Connection')
        if POSTGRES_SSL is True:
            self.pgargs['ssl'] = {
                "check_hostname": True,
                "cafile": POSTGRES_SSL_CA,
                "certfile": POSTGRES_SSL_CERT,
                "keyfile": POSTGRES_SSL_KEY,
            }

    def set_connection(self, conn):
        self._connection = conn

    async def get_connection(self, driver: str = 'pg'):
        """Useful for internal connections of QS.
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.get_running_loop()
        return AsyncDB(
            driver,
            dsn=asyncpg_url,
            loop=loop,
            timeout=int(POSTGRES_TIMEOUT),
            **self.pgargs
        )

    def default_connection(self, driver: str = 'pg', dsn: str = None, params: dict = None):
        """Useful for internal connections of QS.
        """
        if not params:
            params = {}
        args = {}
        if driver == 'pg':
            args = {
                "timeout": int(POSTGRES_TIMEOUT),
                **self.pgargs
            }
            args['server_settings']['application_name'] = 'QS.Read'
        self.logger.debug(
            f"Connection Arguments: {args!s}"
        )
        try:
            connection = AsyncDB(
                driver,
                dsn=dsn,
                loop=self._loop,
                params=params,
                **args
            )
            self.logger.debug(
                f'DSN {driver} > {dsn}'
            )
            return connection
        except Exception as ex:
            self.logger.error(
                f"Error: {ex}"
            )
            raise ConnectionError(
                f"Connection Error: {ex}"
            )

    def get_driver(self, driver) -> DataDriver:
        """Getting a Database Driver from Datasource Drivers.
        """
        if not (drv := self.supported_drivers(driver)):
            raise ConfigError(
                f"QS: Error unsupported Driver: {driver}"
            )
        else:
            if 'driver' in drv:
                return drv['driver']
            else:
                # load dynamically
                clspath = f'querysource.datasources.drivers.{driver}'
                clsname = f'{driver}Driver'
                try:
                    self._dsmodule = import_module(clspath)
                    return getattr(self._dsmodule, clsname)
                except (AttributeError, ImportError) as ex:
                    raise RuntimeError(
                        f"QS: There is no Driver {driver}: {ex}"
                    ) from ex

    def supported_drivers(self, driver):  # pylint: disable=W0613
        try:
            return SUPPORTED[driver]
        except KeyError:
            return False

    async def default_driver(self, driver: str) -> Any:
        """Get a default driver connection.
        """
        if not (self.supported_drivers(driver)):
            raise ConfigError(
                f"QS: Error unsupported Driver: {driver}"
            )
        self.logger.notice(
            f"Getting Default Connection for Driver {driver}"
        )
        default = None
        try:
            if not self._dsmodule:
                # load dynamically
                clspath = f'querysource.datasources.drivers.{driver}'
                self.logger.notice(
                    f"Loading Driver {driver} Module: {clspath}"
                )
                try:
                    self._dsmodule = import_module(clspath)
                except (AttributeError, ImportError) as ex:
                    raise RuntimeError(
                        f"QS: There is no DataSource called {driver}: {ex}"
                    ) from ex
            clsname = f'{driver}_default'
            default = getattr(self._dsmodule, clsname)
        except (AttributeError, ImportError) as ex:
            # No module for driver exists.
            raise RuntimeError(
                f"QS: There is no default connection for Driver {driver}: {ex}"
            ) from ex
        ### creating a connector for this driver:
        driver_type = default.driver_type
        if driver_type == 'asyncdb':
            try:
                return driver_type, AsyncDB(driver, dsn=default.dsn, params=default.params())
            except (DriverError, ProviderError) as ex:
                raise QueryException(
                    f"Error creating AsyncDB instance: {ex}"
                ) from ex
        elif default.driver_type == 'external':
            ## returning -as-is- for use internal by provider
            return driver_type, default
        else:
            ## Other Components.
            return None

    async def get_provider(self, entry: dict):
        """
        Getting a connection from Provider.
        """
        try:
            provider = entry.provider
        except (TypeError, KeyError):
            provider = 'db'
        if provider == 'db':  # default DB connection for Postgres
            _provider = self.load_provider('db')
            conn = self.default_connection(
                driver=self._default_driver, dsn=self._dsn
            )
            # conn = await self._postgres.acquire()
            return [conn, _provider]
        elif provider in EXTERNAL_PROVIDERS:
            _provider = self.load_provider(provider)
            ## TODO: return a QS Provider for REST/External operations
            return [None, _provider]
        if provider in DATASOURCES:
            source, conn = await self.datasource(provider)
            ### get provider from datasource type:
            provider = source.driver
            _provider = self.load_provider(provider)
            ## getting the provider of datasource:
            return [conn, _provider]
        else:
            _provider = self.load_provider(provider)
            # can we use a default driver?
            try:
                _, conn = await self.default_driver(provider)
            except (AttributeError, TypeError, ValueError) as ex:
                print(ex)
                conn = None
            return [conn, _provider]

    def load_provider(self, provider: str) -> BaseProvider:
        """
        Dynamically load a defined Provider.
        """
        if provider in PROVIDERS:
            return PROVIDERS[provider]
        else:
            # Get provider from first time, save it into PROVIDERS dict.
            srcname = f'{provider!s}Provider'
            classpath = f'querysource.providers.{provider}'
            try:
                cls = import_module(classpath, package=srcname)
                obj = getattr(cls, srcname)
                PROVIDERS[provider] = obj
                return obj
            except ImportError as ex:
                raise QueryException(
                    f"Error: No QuerySource Provider {provider} was found: {ex}"
                ) from ex

    async def datasource(self, name: str = 'default'):
        try:
            source = DATASOURCES[name]
        except KeyError:
            return None
        if source.driver_type == 'asyncdb':
            ### making an AsyncDB connection:
            # TODO: adding support for other drivers
            driver = source.driver
            try:
                return source, AsyncDB(
                    driver,
                    dsn=source.dsn,
                    params=source.params()
                )
            except (DriverError, ProviderError) as ex:
                raise QueryException(
                    f"Error creating AsyncDB instance: {ex}"
                ) from ex
        else:
            raise QueryError(
                f'Invalid Datasource type {source.driver_type} for {name}'
            )

    async def get_query_slug(self, slug: str, conn: Any) -> BaseModel:
        try:
            QueryModel.Meta.connection = conn
            self.logger.debug(
                f'::: Getting Slug {slug} from {QueryModel.Meta.schema}.{QueryModel.Meta.name}'
            )
            return await QueryModel.get(query_slug=slug)
        except ValidationError as ex:
            raise SlugNotFound(
                f'Invalid Slug Data {slug!s}: {ex}'
            ) from ex
        except NoDataFound as ex:
            raise SlugNotFound(
                f'Slug not Found {slug!s}'
            ) from ex
        except (ProviderError, DriverError) as ex:
            raise SlugNotFound(
                f"Error getting Slug: {ex}"
            ) from ex

    async def get_slug(self, slug: str, program: str = None):
        start = datetime.now()
        if slug in SLUG_CACHE:
            return SLUG_CACHE[slug]
        if hasattr(self, 'lazy') and self.lazy is True:
            try:
                async with await self._postgres.acquire() as conn:
                    obj = await self.get_query_slug(slug, conn)
            except Exception:  # pylint: disable=W0706
                raise
            finally:
                QueryModel.Meta.connection = None
        else:
            try:
                connection = await self.get_connection(driver='pg')
                async with connection as conn:
                    obj = await self.get_query_slug(slug, conn)
            except Exception:  # pylint: disable=W0706
                raise
            finally:
                QueryModel.Meta.connection = None
        exec_time = (datetime.now() - start).total_seconds()
        self.logger.debug(
            f"Getting Slug, Execution Time: {exec_time:.3f}ms\n"
        )
        if obj is None:
            raise SlugNotFound(
                f'Slug \'{slug}\' not found'
            )
        else:
            SLUG_CACHE[slug] = obj
            return obj
