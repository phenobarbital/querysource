"""Connections.

Manage Database connections and supporting datasources.
"""
import asyncio
from collections.abc import Callable
from typing import Any, Union

from aiohttp import web
from datamodel import BaseModel
from datamodel.parsers.json import JSONContent
from datamodel.exceptions import ValidationError
from asyncdb import AsyncDB, AsyncPool
from asyncdb.exceptions import (
    NoDataFound,
    ProviderError,
    DriverError
)
from asyncdb.utils import cPrint
from navigator.applications.base import BaseApplication
from navigator.types import WebApp
from .conf import (
    POSTGRES_MAX_CONNECTIONS,
    POSTGRES_MIN_CONNECTIONS,
    POSTGRES_TIMEOUT,
    DB_SESSION_TIMEOUT,
    DB_IDLE_IN_TRANSACTION_TIMEOUT,
    asyncpg_url,
    default_dsn,
    QUERYSET_REDIS,
    MEMCACHE_SERVICE
)
from .types import Singleton
import jsonpickle
from .exceptions import (
    ConfigError
)
from .interfaces.connections import (
    Connection,
    DATASOURCES
)

class QueryConnection(Connection, metaclass=Singleton):
    """QueryConnection.

    TODO: QueryConnection will be affected by environment
    (get connection params from enviroment)
    """
    pgargs: dict = {
        "min_size": 2,
        "server_settings": {
            "application_name": "QS.Master",
            "client_min_messages": "notice",
            "jit": "on",
            "effective_cache_size": "2147483647",
            "idle_in_transaction_session_timeout": f"{DB_IDLE_IN_TRANSACTION_TIMEOUT}",
            "idle_session_timeout": f"{DB_SESSION_TIMEOUT}"
        }
    }

    def __init__(self, **kwargs):
        if hasattr(self, '__initialized__'):
            if self.__initialized__ is True:  # pylint: disable=E0203
                return  # already configured.
        Connection.__init__(self, **kwargs)
        self.__initialized__ = True
        self._postgres = None
        self._connected: bool = False
        self.lazy: bool = kwargs.get('lazy', False)
        self._redis: Callable = None
        self._memcached: Callable = None
        self.start_cache(QUERYSET_REDIS)
        self._json = JSONContent()

    def start_cache(self, dsn):
        ### redis connection:
        self._redis = AsyncDB(
            'redis',
            dsn=dsn,
            loop=self._loop
        )
        # memcached connection:
        self._memcached = AsyncDB(
            'memcache',
            params=MEMCACHE_SERVICE,
            loop=self._loop
        )

    def pool(self):
        return self._postgres

    def is_connected(self):
        return bool(self._connected)

    @property
    def connected(self):
        return bool(self._connected)

    async def in_cache(self, key: str) -> Any:
        try:
            async with await self._redis.connection() as conn:
                return await conn.exists(key)
        except (ProviderError, DriverError):
            return False

    async def from_cache(self, key: str) -> Any:
        try:
            async with await self._redis.connection() as conn:
                return await conn.get(key)
        except asyncio.TimeoutError:
            # trying to reconect:
            try:
                self.start_cache(QUERYSET_REDIS)
                async with await self._redis.connection() as conn:
                    return await conn.get(key)
            except Exception as exc:
                self.logger.exception(
                    f"Failure on REDIS Cache: {exc}"
                )
                return False
        except (ProviderError, DriverError):
            return False

    async def acquire(self):
        """acquire.

        Getting a connection from Pool.
        """
        return await self._postgres.acquire()

    def get_connection(self, driver: str = 'pg', evt: asyncio.AbstractEventLoop = None) -> AsyncDB:
        """Useful for internal connections of QS.
        """
        if self.lazy is True:
            self.pgargs['server_settings']['application_name'] = 'QS.Lazy'
        return super().get_connection(driver=driver, evt=evt)

    def setup(self, app: web.Application) -> web.Application:
        if isinstance(app, BaseApplication):  # migrate to BaseApplication (on types)
            self.app = app.get_app()
        elif isinstance(app, WebApp):
            self.app = app  # register the app into the Extension
        else:
            raise TypeError(
                f"Invalid type for Application Setup: {app}:{type(app)}"
            )
        ### startup and shutdown:
        self.app.on_startup.append(
            self.start
        )
        self.app.on_shutdown.append(
            self.stop
        )
        return self.app

    async def start(self, app: Union[web.Application, None] = None):
        """
         Create the default connection for Postgres.
         Create the connection to the database cache (redis).
         Also, reading the existing datasources in a list.
        """
        if not self._loop:
            self._loop = asyncio.get_event_loop()
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = self._loop
        if self.lazy is True:
            self.logger.debug(':: Starting QuerySource in Lazy Mode ::')
            cPrint(
                ':: Starting QuerySource in Lazy Mode ::',
                level='DEBUG'
            )
            # # lazy mode: create a simple database connector
            try:
                self.pgargs['server_settings']['application_name'] = 'QS.Lazy'
                self._connection = AsyncDB(
                    'pg',
                    dsn=asyncpg_url,
                    loop=loop,
                    timeout=int(POSTGRES_TIMEOUT),
                    **self.pgargs
                )
                await self._connection.connection()
            except Exception as err:
                self.logger.exception(err)
                raise ConfigError(
                    f"Unable to Connect to Database. {err}"
                ) from err
        else:
            cPrint(
                ':: Starting QuerySource in Master Mode ::',
                level='DEBUG'
            )
            self.logger.debug(':: Starting QuerySource in Master Mode ::')
            # pgpool (postgres)
            self.pgargs['min_size'] = POSTGRES_MIN_CONNECTIONS
            self.pgargs['max_clients'] = POSTGRES_MAX_CONNECTIONS
            self.logger.debug(' :: Starting PostgreSQL with parameters ::')
            self.logger.debug(f"{self.pgargs!r}")
            try:
                self._postgres = AsyncPool(
                    'pg',
                    dsn=default_dsn,
                    loop=loop,
                    timeout=int(POSTGRES_TIMEOUT),
                    **self.pgargs
                )
                await self._postgres.connect()
            except Exception as err:
                self.logger.exception(err)
                raise
            ## getting all datasources (saved into variable)
            sql = "SELECT * FROM public.datasources;"
            async with await self._postgres.acquire() as conn:
                result, error = await conn.query(sql)
                if error:
                    raise ConfigError(
                        f'Error on Starting QuerySource: {error!s}'
                    )
                for row in result:
                    if row['params'] is None or row['credentials'] is None:
                        self.logger.warning(
                            f"DataSource Error {row['name']}: Missing Credentials."
                        )
                        continue
                    # building a datasource based on driver:
                    name = row['name']
                    try:
                        driver = self.get_driver(row['driver'])
                    except Exception as ex:  # pylint: disable=W0703
                        self.logger.exception(ex, stack_info=True)
                        continue
                    try:
                        # TODO: encrypting credentials in database:
                        if row['dsn']:
                            data = {
                                "dsn": row['dsn']
                            }
                        else:
                            try:
                                data = {
                                    **dict(row['params']),
                                }
                            except TypeError:
                                data = dict(row['params'])
                            for key, val in row.get('credentials', {}).items():
                                data[key] = await self.get_from_env(
                                    key_name=val,
                                    default=val
                                )
                            for key, val in row.get('params', {}).items():
                                data[key] = await self.get_from_env(
                                    key_name=val,
                                    default=val
                                )
                        DATASOURCES[name] = driver(**data)
                    except (ValueError) as ex:
                        self.logger.exception(
                            ex,
                            stack_info=False
                        )
                        continue
                    except ValidationError as ex:
                        self.logger.exception(
                            (
                                f"Datasource validation error: {ex} "
                                f"Error: {ex.payload}"
                            ),
                            stack_info=False
                        )
                        continue
                    # SAVING DATASOURCES IN MEMORY (memcached)
                    try:
                        async with await self._memcached.connection() as conn:
                            ds = jsonpickle.encode(DATASOURCES[name])
                            await conn.set(name, ds)
                    except (ProviderError, DriverError, TypeError, ValueError) as ex:
                        self.logger.warning(
                            str(ex)
                        )
        app['qs_connection'] = self
        self._connected = True

    async def query_table_exists(self, connection: Callable, program: str) -> bool:
        sql = f"SELECT EXISTS ( \
                       SELECT FROM pg_catalog.pg_class c \
                       JOIN   pg_catalog.pg_namespace n ON n.oid = c.relnamespace \
                       WHERE  n.nspname = '{program}' \
                       AND    c.relname = 'tasks' \
                       AND    c.relkind = 'r');"
        row = await connection.fetchval(sql, column='exists')
        if row:
            return True
        else:
            return False

    async def dispose(self, conn: Callable = None):
        """
        dispose a connection from the pg pool.
        """
        # self.logger.debug('Disposing a Query Connection')
        if conn:
            # TODO: check if connection is from instance pg
            try:
                await self._postgres.release(conn)
            except Exception:  # pylint: disable=W0703
                await conn.close()

    async def stop(self, app: web.Application = None):
        """
        stop.
           Close and dispose all connections
        """
        self.logger.debug(':: Closing all Querysource connections ::')
        try:
            if self.lazy is True:
                await self._connection.close()
            else:
                await self._postgres.wait_close(gracefully=True, timeout=10)
                # await self._postgres.close(timeout=10)
        except RuntimeError as err:
            self.logger.exception(err)
        except Exception as err:
            self.logger.exception(err)
            raise
        self.logger.debug('Exiting ...')
        self._connected = False
