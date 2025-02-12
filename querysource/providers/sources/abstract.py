"""Abstract Class for HTTP/REST Data Sources.
"""
import os
import sys
from typing import (
    Any,
    Union,
    Optional
)
import copy
from abc import ABC, abstractmethod
from collections.abc import Callable
import asyncio
from aiohttp import web
from navconfig import config
from navconfig.logging import logging
from ...models import QueryModel
from ...libs.encoders import DefaultEncoder
from ...exceptions import ParserError
from ...utils.functions import get_hash
if sys.version_info < (3, 10):
    from typing_extensions import ParamSpec
else:
    from typing import ParamSpec


P = ParamSpec("P")


class baseSource(ABC):
    """baseSource.

    Description: Base class for all QS Sources.
    """
    __parser__: Optional[Callable] = None
    content_type: Optional[str] = None

    def __init__(
        self,
        *args: P.args,
        slug: str = None,
        query: Any = None,
        qstype: str = '',  # migrate to Enum
        definition: Union[QueryModel, dict] = None,  # Model Object or a dictionary defining a Query.
        conditions: dict = None,
        request: web.Request = None,
        loop: asyncio.AbstractEventLoop = None,
        **kwargs: P.kwargs
    ) -> None:
        self.logger = logging.getLogger(f'QS.Source.{__name__}')
        self._definition = definition  # definition Object
        if not conditions:
            self._conditions = {}
        else:
            self._conditions: dict = copy.deepcopy(conditions)
        # Aiohttp Request:
        self._request: web.Request = request
        ### basic information
        self._slug: str = slug
        self._type: str = qstype
        if self._slug:
            try:
                self._query = definition.query_raw
            except AttributeError:
                self._query = None
        else:
            # is a raw query
            self._query: str = query
        # origin:
        self._origin = query
        if hasattr(self, 'method'):
            self.method = self.method.lower()
        else:
            self.method = kwargs.pop('method', 'get')
        # Event Loop.
        self._loop = loop or asyncio.get_event_loop()
        ## Timeout:
        try:
            ts = definition.params.get('timeout', 60)
        except AttributeError:
            ts = 60
        self.timeout = kwargs.pop('timeout', ts)
        self.args = args
        self.kwargs = kwargs
        self._encoder = DefaultEncoder()
        # Config Object:
        self._env = config
        self._parser: Callable = None
        if self.__parser__:
            self.logger.debug(
                f"Loading Parser: {self.__parser__}"
            )
            try:
                self._parser = self.__parser__(  # pylint: disable=E1102
                    query=self._query,
                    definition=definition,
                    conditions=conditions
                )
            except Exception as err:
                self.logger.error(
                    f'ERROR ON PARSER: {err}'
                )
                raise ParserError(
                    "Error on Query Parser: {err}"
                ) from err

    def __post_init__(
        self,
        definition: dict,
        conditions: dict,
        request: Any = None,
        **kwargs
    ) -> None:
        pass

    def build_url(self, url, queryparams: str = None, args: dict = None):
        if isinstance(args, dict):
            u = url.format(**args)
        else:
            u = url
        if queryparams:
            if '?' in u:
                full_url = u + '&' + queryparams
            else:
                full_url = u + '?' + queryparams
        else:
            full_url = u
        self.logger.debug(f'Resource URL: {full_url!s}')
        return full_url

    def get_env_value(self, key, default: str = None):
        if val := os.getenv(key):
            return val
        elif val := self._env.get(key, default):
            return val
        else:
            return key

    def result(self):
        return self._result

    @property
    def parser(self):
        return self._parser

    def checksum(self):
        """cheksum.
        Get cheksum for URL parameters
        """
        params = {
            'origin': self._origin,
            "method": self.method,
            'params': self._conditions
        }
        data = self._encoder.dumps(params)
        # get hash from parameters (same url get the same results)
        return get_hash(data)

    @abstractmethod
    async def query(self, url: str = None, params: dict = None):
        pass

    def get(self):
        if self._loop:
            return self._loop.run_until_complete(
                self.query()
            )
        return asyncio.run(
            self.query()
        )

    def accepts(self) -> str:
        return self.content_type
