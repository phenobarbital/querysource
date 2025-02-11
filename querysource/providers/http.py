import sys
from typing import (
    Any,
    Union
)
import importlib
from importlib import import_module
import urllib3
from aiohttp import web
from navconfig.logging import logging
from asyncdb.exceptions import ProviderError, NoDataFound
from ..models import QueryModel
from ..utils.functions import check_empty
from ..exceptions import (
    DataNotFound,
    QueryException,
    DriverError
)
from .abstract import BaseProvider

urllib3.disable_warnings()
logging.getLogger("urllib3").setLevel(logging.WARNING)


class httpProvider(BaseProvider):
    __parser__ = None

    def __init__(
        self,
        slug: str = '',
        query: Any = None,
        qstype: str = '',
        connection: Any = None,
        definition: Union[QueryModel, dict] = None,  # Model Object or a dictionary defining a Query.
        conditions: dict = None,
        request: web.Request = None,
        **kwargs
    ):
        self._url = kwargs.pop('url', '')
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
                    f"HTTP Error, no dialect was found: {ex}"
                ) from ex

    async def prepare_connection(self):
        await super(httpProvider, self).prepare_connection()
        module_name = f'querysource.providers.sources.{self.dialect}'
        try:
            module = import_module(module_name, package='sources')
        except SyntaxError as err:
            raise DriverError(
                f"Syntax Error over {self.dialect}: {err}"
            ) from err
        except ModuleNotFoundError:
            try:
                module_name = f'querysource.plugins.sources.{self.dialect}'
                module = import_module(module_name, package='sources')
            except ModuleNotFoundError as err:
                # Use base HTTP Source instead.
                module_name = 'querysource.providers.sources.http'
                try:
                    module = importlib.import_module(module_name, package='sources')
                except SyntaxError as err:
                    raise QueryException(
                        f"Error: Syntax Error on HTTPSource: {err}"
                    ) from err
                except ModuleNotFoundError:
                    ### try to find Module in plugins folder:
                    try:
                        module_name = f'querysource.plugins.sources.{self.dialect}'
                        module = importlib.import_module(module_name, package='sources')
                    except ModuleNotFoundError as err:
                        raise QueryException(
                            f'Error importing {module_name} module, error: {str(err)}'
                        ) from err
        try:
            class_name = getattr(module, self.dialect, 'httpSource')
            self._source = class_name(
                definition=self._definition,
                conditions=self._conditions,
                request=self._request,
                loop=self._loop
            )
        except Exception as err:
            raise QueryException(
                f'Exception calling {module_name}, error: {str(err)}'
            ) from err
        # refresh proxies
        await self._source.refresh_proxies()

    def accepts(self) -> str:
        """accepts.
        Check the Mime Type of response to be returned by OutputFactory.
        """
        return self._source.accepts()

    async def result(self):  # pylint: disable=W0236
        """result.
        get the result from the Provider Source.
        """
        # preparing any connection (if needed)
        await self.prepare_connection()
        result = None
        attr = self.kwargs.get('attribute', 'query')
        try:
            query = getattr(self._source, attr)
        except AttributeError as ex:
            raise DriverError(
                f"{self._source} has no Attribute Function: {attr}"
            ) from ex
        try:
            result = await query()
            if check_empty(result):
                raise NoDataFound(
                    "Data no was found"
                )
            return result
        except (RuntimeError, ProviderError) as err:
            raise DriverError(
                str(err)
            ) from err

    async def columns(self):
        """
        columns.
        Get the columns of result
        """
        raise NotImplementedError("Columns is not implemented on HTTP Source.")

    async def query(self):
        """query.
        Get data from HTTP API
        """
        result = []
        error = None
        try:
            result = await self.result()
        except (DataNotFound, NoDataFound) as err:
            error = err
        except (RuntimeError, ProviderError) as err:
            print(f"Querysource HTTP Error: {err}")
            raise ProviderError(
                f"Querysource HTTP Error: {err}"
            ) from err
        except Exception as err:
            raise QueryException(
                f'Exception calling HTTP: {err}'
            ) from err
        if result:
            self._dict = result
            self._result = result
            self._parser = self._source.parser
            return [result, error]
        else:
            raise NoDataFound("HTTP: No Data was Found")

    async def close(self):
        """close.
        Close connection to HTTP API
        """
        try:
            await self._source.close()
        except (ProviderError, DriverError, RuntimeError) as err:
            self._logger.exception(err, stack_info=True)
