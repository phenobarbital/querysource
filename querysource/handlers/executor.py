"""
QueryExecutor.

Handler to making queries directly to providers.
"""
# for aiohttp
from aiohttp import web
import logging
from ..exceptions import (
    QueryException,
    QueryError
)
from ..queries.executor import Executor
from ..auth import ResourceType
from .abstract import AbstractHandler


logging.getLogger('pymongo').setLevel(logging.WARNING)

class QueryExecutor(AbstractHandler):
    """Executor.
        Description: Arbitrary Query Executor.
    """
    def default_headers(self) -> dict:
        return {
            'X-STATUS': 'OK',
            'X-MESSAGE': 'Query Execution'
        }

    async def _enforce_payload(
        self,
        request: web.Request,
        data: dict,
        query: "Executor",
    ) -> None:
        """Run pre-execution PBAC checks for executor payloads.

        Branches on slug vs raw-query based on the ``slug`` key in ``data``.
        Then enforces datasource and driver checks using resolved attrs on the
        query object after ``Executor.start()`` has populated ``_query``.

        Args:
            request: The current aiohttp web request.
            data: Raw payload dict from ``get_payload()``.
            query: ``Executor`` instance after ``start()`` has been called.

        Raises:
            web.HTTPNotFound: When any PBAC check denies access.
        """
        # Slug check vs raw-query check.
        slug = (data or {}).get('slug') if isinstance(data, dict) else None
        if slug:
            await self._enforce_pbac(
                request,
                resource_type=ResourceType.SLUG,
                resource_name=slug,
                action="slug:execute",
            )
        else:
            await self._enforce_pbac(
                request,
                resource_type=ResourceType.RAW_QUERY,
                resource_name="raw_query",
                action="raw_query:execute",
            )
        # Datasource + driver checks (best-effort: attrs populated by start()).
        inner = getattr(query, '_query', None)
        ds = getattr(inner, 'datasource', None)
        drv = getattr(inner, 'driver', None)
        if ds:
            await self._enforce_pbac(
                request,
                resource_type=ResourceType.DATASOURCE,
                resource_name=ds,
                action="datasource:use",
            )
        if drv:
            await self._enforce_pbac(
                request,
                resource_type=ResourceType.DRIVER,
                resource_name=drv,
                action="driver:use",
            )

    async def get_payload(self, request: web.Request) -> dict:
        data = None
        if request.content_type == 'application/json':
            # getting json data and converted to Query Object
            try:
                data = await self.get_json(request)
            except (ValueError, TypeError):
                data = await self.body(request)
        else:
            # get directly query from raw data:
            data = await self.body(request)
        return data

    def get_executor(self, data, request: web.Request) -> Executor:
        try:
            query = Executor(request=request)
        except Exception as ex:
            print(f'Error Loading Executor : {ex}')
            raise
        query.start(data)
        return query

    async def query(self, request):
        """query.
        Description: get a Query Object a making a query to Backend.
        Args:
            request (web.Request, optional): HTTP Web Request. Defaults to None.
        """
        payload = await self.get_payload(request)
        if not payload:
            return self.error(
                response='QS: Missing Query object or sentence to run.',
                status=410  # bad request
            )
        ##
        # build a query Executor Object with the query
        try:
            query = self.get_executor(payload, request)
        except QueryError as ex:
            return self.error(
                response=ex.message,
                status=ex.code  # bad request
            )
        except Exception as ex:
            self.logger.error(str(ex), stack_info=True)
            return self.critical(
                reason={"message": str(ex)},
                status=500
            )
        # PBAC: enforce before any query execution starts.
        await self._enforce_payload(request, payload, query)
        try:
            obj = await query.query()
            return self.json_response(
                response=obj,
                status=200,
                headers=self.default_headers()
            )
        except KeyError as ex:
            return self.error(
                response=f'Missing required field: {ex}',
                status=400  # bad request
            )
        except (QueryError, QueryException) as ex:
            return self.error(
                response=ex.message,
                status=ex.code  # bad request
            )
        except Exception as ex:
            self.logger.error(str(ex), stack_info=True)
            return self.critical(
                reason={"message": str(ex)},
                exception=ex,
                status=500
            )


    async def dry_run(self, request: web.Request = None):
        """dry_run.
        Description: get a Query Object, check if valid, and only ruturns a false response.
        Args:
            request (web.Request, optional): HTTP Web Request. Defaults to None.
        """
        data = await self.get_payload(request)
        ##
        if data:
            # build a query Executor Object with the query
            try:
                query = self.get_executor(data, request)
            except QueryError as ex:
                return self.error(
                    response=ex.message,
                    status=ex.code  # bad request
                )
            except Exception as ex:
                self.logger.error(str(ex), stack_info=True)
                return self.critical(
                    reason={"message": str(ex)},
                    status=500
                )
            # PBAC: enforce before any dry_run execution starts.
            await self._enforce_payload(request, data, query)
            try:
                obj = await query.dry_run()
                return self.json_response(
                    response=obj,
                    status=200,
                    headers=self.default_headers()
                )
            except (QueryError, QueryException) as ex:
                return self.error(
                    response=ex.message,
                    status=ex.code  # bad request
                )
            except Exception as ex:
                self.logger.error(str(ex), stack_info=True)
                return self.critical(
                    reason={"message": str(ex)},
                    status=500
                )
        else:
            return self.error(
                response='QS: Missing Query object or sentence to run.',
                status=410  # bad request
            )
