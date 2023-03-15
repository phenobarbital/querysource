import time
import asyncio
import threading
from aiohttp import web
from querysource.outputs import DataOutput
from querysource.exceptions import (
    ParserError,
    DataNotFound,
    DriverError,
    QueryException,
    SlugNotFound,
)
from querysource.queries.obj import QueryObject
from .abstract import AbstractHandler
from .operators import Join, Concat
from .transformations import crosstab, correlation

class ThreadQuery(threading.Thread):
    def __init__(self, name: str, query: dict, request: web.Request, queue: asyncio.Queue):
        super().__init__()
        self._loop = asyncio.new_event_loop()
        self._queue = queue
        self.exc = None
        # I need to build a QueryObject task, and put arguments on there.
        self._query = QueryObject(name, query, queue=queue, request=request, loop=self._loop)

    def slug(self):
        return self._query.slug

    def run(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(
                self._query.build_provider()
            )
        except Exception as ex:
            self.exc = ex
        try:
            self._loop.run_until_complete(
                self._query.query()
            )
        except Exception as ex:
            self.exc = ex

class QueryHandler(AbstractHandler):

    async def query(self, request: web.Request) -> web.StreamResponse:
        total_time = 0
        started_at = time.monotonic()
        options = {}
        params = self.query_parameters(request)
        args = self.match_parameters(request)
        writer_options = {}
        _format: str = 'json'
        try:
            _format = args['meta'].replace(':', '')
        except KeyError:
            pass
        try:
            options = await self.json_data(request)
        except (TypeError, ValueError):
            options = {}
        if not 'queries' in options:
            raise self.Error(
                message='Invalid POST Option passed to MultiQuery.',
                code=400
            )
        # get the format: returns a valid MIME-Type string to use in DataOutput
        try:
            if 'queryformat' in params:
                _format = params['queryformat']
                del params['queryformat']
        except KeyError:
            pass
        # extracting params from FORMAT:
        try:
            _format, tpl = _format.split('=')
        except ValueError:
            tpl = None
        if tpl:
            try:
                report = options['_report_options']
            except (TypeError, KeyError):
                report = {}
            writer_options = {
                "template": tpl,
                **report
            }
        try:
            writer_options = options['_output_options']
            del options['_output_options']
        except (TypeError, KeyError):
            pass
        try:
            del options['_csv_options']
        except (TypeError, KeyError):
            pass
        queryformat = self.format(request, params, _format)
        output_args = {
            "writer_options": writer_options,
        }
        # creates the Result Queue:
        result_queue = asyncio.Queue()
        tasks = {}
        for name, query in options['queries'].items():
            t = ThreadQuery(name, query, request, result_queue)
            t.start()
            tasks[name] = t
        ## then, run all jobs:
        for _, t in tasks.items():
            t.join()
            if t.exc:
                ## raise exception for this Task
                if isinstance(t.exc, SlugNotFound):
                    raise self.Error(
                        message=f"Slug Not Found: {t.slug()}",
                        exception=t.exc,
                        code=404
                    )
                elif isinstance(t.exc, ParserError):
                    raise self.Error(
                        message=f"Error parsing Query Slug {t.slug()}",
                        exception=t.exc
                    )
                elif isinstance(t.exc, (QueryException, DriverError)):
                    raise self.Error(
                        message="Query Error",
                        exception=t.exc
                    )
                else:
                    raise self.Except(
                        message=f"Error on Query: {t!s}",
                        exception=t.exc
                    )
        result = {}
        while not result_queue.empty():
            result.update(await result_queue.get())
        ### Step 2: passing Results to JOIN virtuals
        if 'Join' in options:
            try:
                ## making Join of Data
                join = Join(data=result, **options['Join'])
                result = await join.run()
            except (QueryException, Exception) as ex:
                raise self.Except(
                    message="Error on JOIN",
                    exception=ex
                ) from ex
        if 'Concat' in options:
            try:
                ## making Join of Data
                concat = Concat(data=result, **options['Concat'])
                result = await concat.run()
            except (QueryException, Exception) as ex:
                raise self.Except(
                    message="Error on Concat",
                    exception=ex
                ) from ex
        ### Step 3: passing result to Transformations
        if 'Transform' in options:
            # passing the resultset for several transformation rules.
            ## TODO: logic for calling components:
            for step in options['Transform']:
                obj = None
                for step_name, component in step.items():
                    if step_name == 'crosstab':
                        obj = crosstab(data=result, **component)
                        result = await obj.run()
                    elif step_name == 'correlation':
                        obj = correlation(data=result, **component)
                        result = await obj.run()
                break
        if 'Processors' in options:
            pass
        ### Step 4: Passing result to any Processor declared
        ### Step 5: passing Result to DataOutput
        try:
            output = DataOutput(request, query=result, ctype=queryformat, slug=None, **output_args)
            total_time = time.monotonic() - started_at
            self.logger.debug(
                f'Query Duration: {total_time:.2f} seconds'
            )
            return await output.response()
        except (DriverError, DataNotFound) as err:
            raise self.Error(
                message="DataOutput Error",
                exception=err,
                code=402
            )
        except (QueryException, Exception) as ex:
            raise self.Except(
                message="Error on Query",
                exception=ex
            ) from ex
