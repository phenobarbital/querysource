"""BaseQuery.

Base Class for all Query-objects in QuerySource.
"""
import asyncio
from abc import abstractmethod
from typing import Union, Optional
from datamodel.exceptions import ValidationError
from aiohttp import web
from navconfig.logging import logging
from ..interfaces.queries import AbstractQuery
from ..outputs.dt import OutputFactory
from .models import Query, QueryResult


logging.getLogger('visions.backends').setLevel(logging.WARNING)
logging.getLogger('matplotlib').setLevel(logging.WARNING)

class BaseQuery(AbstractQuery):

    def __init__(
            self,
            slug: str = None,
            conditions: dict = None,
            request: web.Request = None,
            loop: Optional[asyncio.AbstractEventLoop] = None,
            **kwargs
    ):
        """
        Initialize the Query Object
        """
        super(BaseQuery, self).__init__(
            slug=slug,
            conditions=conditions,
            request=request,
            loop=loop,
            **kwargs
        )

    async def __aenter__(self):
        """Async context manager entry - builds the provider."""
        await self.build_provider()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - closes resources."""
        await self.close()
        return False

    def query_model(self, data: Union[str, dict]) -> Query:
        if isinstance(data, str):
            q = {
                "query": data
            }
        else:
            q = data
        try:
            return Query(**q)
        except (ValueError, TypeError, ValidationError) as ex:
            raise TypeError(
                f"Invalid Query Object: {ex}"
            ) from ex

    def get_result(
        self,
        query: Query,
        data: Optional[Union[list, dict]],
        duration: float,
        errors: list = None,
        state: str = None
    ) -> QueryResult:
        if query.raw_result:
            return data
        try:
            return QueryResult(
                driver=query.driver,
                query=query.query,
                duration=duration,
                errors=errors,
                data=data,
                state=state
            )
        except (TypeError, ValueError) as ex:
            raise TypeError(
                f"Invalid data for QueryResult: {ex}"
            ) from ex
        except ValidationError as ex:
            print(ex, ex.payload)
            errors = ex.payload
            raise TypeError(
                f"Invalid data for QueryResult: {errors}"
            ) from ex

    async def build_provider(self):
        """build_provider.

        Create and configure the provider for this query.
        Override in subclasses if provider setup is needed.
        """
        pass

    async def close(self):
        """close.

        Close and cleanup resources used by the query.
        Override in subclasses if cleanup is needed.
        """
        pass

    @abstractmethod
    async def query(self):
        """query.

        Run an arbitrary query in async mode.
        """

    async def output(self, result, error):
        # return result in default format
        self._result = result
        return [result, error]

    def output_format(self, frmt: str = 'native', **kwargs):  # pylint: disable=W1113
        self._output_format = OutputFactory(
            self,
            frmt=frmt,
            **kwargs
        )
