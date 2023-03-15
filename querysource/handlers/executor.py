"""
QueryExecutor.

Handler to making queries directly to providers.
"""
# for aiohttp
from aiohttp import web
from querysource.exceptions import (
    QueryException,
    QueryError
)
from querysource.queries.executor import Executor
from .abstract import AbstractHandler


class QueryExecutor(AbstractHandler):
    """Executor.
        Description: Arbitrary Query Executor.
    """
    def default_headers(self) -> dict:
        return {
            'X-STATUS': 'OK',
            'X-MESSAGE': 'Query Execution'
        }

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
            query = Executor(request = request)
        except Exception as ex:
            print(ex)
            raise
        query.start(data)
        return query

    async def query(self, request):
        """query.
        Description: get a Query Object a making a query to Backend.
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
                    status=ex.code # bad request
                )
            except Exception as ex:
                self.logger.error(str(ex), stack_info=True)
                return self.critical(
                    reason={"message": str(ex)},
                    status=500
                )
            try:
                obj = await query.query()
                return self.json_response(
                    response=obj,
                    status=200,
                    headers=self.default_headers()
                )
            except (QueryError, QueryException) as ex:
                return self.error(
                    response=ex.message,
                    status=ex.code # bad request
                )
            except Exception as ex:
                self.logger.error(str(ex), stack_info=True)
                return self.critical(
                    reason={"message": str(ex)},
                    exception=ex,
                    status=500
                )
        else:
            return self.error(
                response='QS: Missing Query object or sentence to run.',
                status=410 # bad request
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
                    status=ex.code # bad request
                )
            except Exception as ex:
                self.logger.error(str(ex), stack_info=True)
                return self.critical(
                    reason={"message": str(ex)},
                    status=500
                )
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
                    status=ex.code # bad request
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
                status=410 # bad request
            )
