from typing import Any, Union
from importlib import import_module
from aiohttp import web
from asyncdb.exceptions import ProviderError, NoDataFound
from querysource.models import QueryModel
from querysource.exceptions import (
    DataNotFound,
    DriverError,
    QueryException
)
from .http import httpProvider


class restProvider(httpProvider):
    __parser__ = None

    def __init__(
        self,
        slug: str = '',
        query: Any = None,
        qstype: str = '',
        connection: Any = None,
        definition: Union[QueryModel, dict] = None, # Model Object or a dictionary defining a Query.
        conditions: dict = None,
        request: web.Request = None,
        **kwargs
    ):
        super(httpProvider, self).__init__(
            slug=slug,
            query=query,
            qstype=qstype,
            connection=connection,
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )
        ## getting dialect:
        self.dialect: str = None
        if definition:
            self.dialect = definition.params['dialect']
        else:
            try:
                self.dialect = kwargs['source']
                del kwargs['source']
            except (KeyError, AttributeError) as ex:
                raise DriverError(
                    f"REST Error, no dialect was found: {ex}"
                ) from ex

    async def prepare_connection(self):
        await super(restProvider, self).prepare_connection()
        module_name = f'querysource.providers.sources.{self.dialect}'
        try:
            module = import_module(module_name, package='sources')
        except SyntaxError as err:
            raise DriverError(
                f"Syntax Error over {self.dialect}: {err}"
            ) from err
        except ImportError:
            ## check if can be loaded from other place:
            module_name = f'querysource.plugins.sources.{self.dialect}'
            try:
                module = import_module(module_name, package='sources')
            except ImportError as ex:
                self._logger.error(
                    f'Error importing REST Dialect {self.dialect}'
                )
                raise DriverError(
                    f"Error importing REST {self.dialect}: {ex}"
                ) from ex
        except Exception as err:
            raise QueryException(
                f'Error: Unknown Error on Dialect {self.dialect}, error: {str(err)}'
            ) from err

        try:
            class_name = getattr(module, self.dialect)
            self._source = class_name(
                definition=self._definition,
                conditions=self._conditions,
                request=self._request,
                # loop=self._loop,
                **self.kwargs
            )
        except Exception as err:
            raise DriverError(
                f'Exception calling {self.dialect}, error: {str(err)}'
            ) from err

    async def result(self): # pylint: disable=W0236
        """result.
           get the result from the Source REST.
        """
        # preparing any connection (if needed)
        result = None
        try:
            attr = self.kwargs['attribute']
        except KeyError:
            attr = 'query'
        try:
            query = getattr(self._source, attr)
        except AttributeError as ex:
            raise DriverError(
                f"{self._source} has no Attribute Function: {attr}"
            ) from ex
        try:
            result = await query()
            if not result:
                raise DataNotFound('No Data was found')
            return result
        except DataNotFound:
            raise
        except (RuntimeError, ProviderError) as err:
            raise DriverError(
                str(err)
            ) from err

    async def query(self):
        """
        query
           get data from REST API
        """
        result = []
        error = None
        try:
            result = await self.result()
        except (DataNotFound, NoDataFound) as err:
            error = err
        except (RuntimeError, ProviderError, DriverError) as err:
            self._logger.error(f"Querysource REST Error: {err}")
            raise DriverError(
                f"Querysource REST Error: {err}"
            ) from err
        if result:
            self._dict = result
            self._result = result
            return [result, error]
        else:
            raise DataNotFound(
                message=f"{self.dialect}: No Data was Found"
            )

    async def dry_run(self):
        return await super().dry_run()
