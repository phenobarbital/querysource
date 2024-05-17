import time
import asyncio
import threading
from aiohttp import web
from ..outputs import DataOutput
from ..exceptions import (
    ParserError,
    DataNotFound,
    DriverError,
    QueryException,
    SlugNotFound,
)
from .abstract import AbstractHandler
from .operators import Join, Concat, Melt
from .transformations import (
    crosstab,
    correlation,
    GoogleMaps,
    Forecast,
    Map
)
from .outputs import TableOutput
from .sources import ThreadQuery, ThreadFile

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
        ## Getting data from Queries or Files
        _queries = options.get('queries', {})
        _files = options.get('files', {})
        if not (_queries or _files):  # Check if both are effectively empty
            print('AQUI ')
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
        if _queries:
            for name, query in _queries.items():
                t = ThreadQuery(name, query, request, result_queue)
                t.start()
                tasks[name] = t
        if _files:
            for name, file in _files.items():
                t = ThreadFile(name, file, request, result_queue)
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
        if 'Melt' in options:
            try:
                ## making Join of Data
                melt = Melt(data=result, **options['Melt'])
                result = await melt.run()
            except (QueryException, Exception) as ex:
                raise self.Except(
                    message=f"Error on Melting Data: {ex}",
                    exception=ex
                ) from ex
        else:
            # Fallback is to passing one single Dataframe:
            if len(result.values()) == 1:
                result = list(result.values())[0]
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
                    elif step_name == 'GoogleMaps':
                        obj = GoogleMaps(data=result, **component)
                        result = await obj.run()
                    elif step_name == 'Forecast':
                        obj = Forecast(data=result, **component)
                        result = await obj.run()
                    elif step_name == 'Map':
                        obj = Map(data=result, **component)
                        result = await obj.run()
                continue
        if 'Processors' in options:
            pass
        ### Step 4: Check if result is empty or is a dictionary of dataframes:
        if result is None:
            raise self.Error(
                message="Empty Result",
                code=404
            )
        # reduce to one single Dataframe:
        if isinstance(result, dict) and len(result) == 1:
            result = list(result.values())[0]
        # TODO: making a melt of all dataframes
        ### Step 5: Passing result to any Processor declared
        if 'Output' in options:
            ## Optionally saving result into Database (using Pandas)
            for step in options['Output']:
                obj = None
                for step_name, component in step.items():
                    if step_name == 'tableOutput':
                        obj = TableOutput(data=result, **component)
                        result = await obj.run()
        ### Step 5: passing Result to DataOutput
        try:
            output = DataOutput(
                request,
                query=result,
                ctype=queryformat,
                slug=None,
                **output_args
            )
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
