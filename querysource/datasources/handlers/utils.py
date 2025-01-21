from aiohttp import web
from asyncdb import AsyncDB
from asyncdb.exceptions import ProviderError, DriverError
from navigator.views import BaseHandler
from ..drivers import SUPPORTED
from ...exceptions import DriverException


class DatasourceDrivers(BaseHandler):
    """Function utilities for managing QS Drivers.
    """

    async def supported_drivers(self, request: web.Request, **kwargs):
        """supported_drivers.
           description: returns a list of all supported drivers
        """
        headers = {
            'X-STATUS': 'OK',
            'X-MESSAGE': 'Drivers List'
        }
        drivers = {k: v['driver'].properties() for k, v in SUPPORTED.items()}
        return self.json_response(response=drivers, headers=headers)

    def driver_information(self, request: web.Request):
        try:
            arguments = self.get_arguments(request)
            driver = arguments['driver']
        except Exception as err: # pylint: disable=W0703
            raise ValueError(
                f'Datasource: there is no *driver* argument in call {err}.'
            ) from err
        try:
            obj = SUPPORTED[driver]
        except KeyError as ex:
            raise DriverException(
                f'Unsupported QS driver {driver}',
            ) from ex
        return (driver, obj)

    async def get_driver(self, request: web.Request):
        try:
            driver, obj = self.driver_information(request)
        except (TypeError, DriverError) as err:
            return self.error(
                request,
                str(err),
                state=400
            )
        headers = {
            'X-STATUS': 'OK',
            'X-MESSAGE': f'{driver} Driver Information'
        }
        return self.json_response(response=obj['driver'].properties(), headers=headers)

    async def check_credentials(self, request: web.Request):
        try:
            driver, obj = self.driver_information(request)
        except (TypeError, DriverError) as err:
            return self.error(
                request,
                str(err),
                state=400
            )
        # get connection parameters from data:
        try:
            params = await self.json_data(request)
            cls = obj['driver'](**params)
        except (ValueError, TypeError) as err:
            return self.error(
                f'Error parsing Connection parameters: {err}',
                exception=err,
                state=400
            )
        if not cls.is_valid():
            return self.error(
                f'Connection params are invalid for this driver: {driver}',
                state=401
            )
        # simply return true as success.
        response = {
            "driver": cls.driver,
            "status": True,
            "args": params
        }
        headers = {
            'X-STATUS': 'OK',
            'X-MESSAGE': f'{driver} Test Information'
        }
        return self.json_response(response=response, headers=headers)

    async def test_connection(self, request: web.Request):
        try:
            driver, obj = self.driver_information(request)
        except (TypeError, DriverError) as err:
            return self.error(
                request,
                str(err),
                state=400
            )
        # get connection parameters from data:
        try:
            params = await self.json_data(request)
            cls = obj['driver'](**params)
        except (ValueError, TypeError) as err:
            return self.error(
                f'Error parsing Connection parameters: {err}',
                exception=err,
                state=400
            )
        if not cls.is_valid():
            return self.error(
                f'Connection params are invalid for this driver: {driver}',
                state=401
            )
        result = None
        errors = None
        state = False
        # checking type of Datasource:
        if cls.driver_type == 'asyncdb':
            args = {
                "dsn": cls.dsn,
                "params": cls.params()
            }
            try:
                db = AsyncDB(
                    cls.driver,
                    loop=self._loop,
                    timeout=10,
                    **args
                )
            except (RuntimeError, TypeError) as ex:
                return self.critical(
                    response=f"Error loading AsyncDB driver {cls.driver}: {ex}",
                    state=500
                )
            except (ProviderError, DriverError) as ex:
                response = {
                    "driver": cls.driver,
                    "params": cls.get_parameters(),
                    "connected": state,
                    "errors": str(ex)
                }
                return self.error(
                    response=response,
                    state=400
                )
            # test connection:
            try:
                async with await db.connection() as conn:
                    state = db.is_connected()
                    result, error = await conn.test_connection()
                    if result and not error:
                        state = True
                    if error:
                        state = False
                        errors = error
            except (ProviderError, DriverError) as ex:
                response = {
                    "driver": cls.driver,
                    "params": cls.get_parameters(),
                    "connected": state,
                    "errors": str(ex)
                }
                return self.error(
                    response=response,
                    state=400
                )
            except Exception as ex:
                response = {
                    "driver": cls.driver,
                    "params": cls.get_parameters(),
                    "connected": state,
                    "errors": str(ex)
                }
                return self.error(
                    response=response,
                    state=400
                )
        response = {
            "driver": cls.driver,
            "params": cls.get_parameters(),
            "connected": state,
            "errors": errors
        }
        headers = {
            'X-STATUS': 'OK',
            'X-MESSAGE': f'{driver} Test Information'
        }
        return self.json_response(response=response, headers=headers)
