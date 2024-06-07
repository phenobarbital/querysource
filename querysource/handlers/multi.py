import time
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
from ..queries import MultiQS
from .outputs import TableOutput


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
        ## Step 1: Running all Queries and Files on QueryObject
        qs = MultiQS(
            queries=_queries,
            files=_files,
            query=options
        )
        try:
            result = await qs.query()
        except SlugNotFound as snf:
            raise self.Error(
                message="Slug Not Found",
                exception=snf,
                code=404
            )
        except ParserError as pe:
            raise self.Error(
                message="Error parsing Query Slug",
                exception=pe,
                code=401
            )
        except (QueryException, DriverError) as qe:
            raise self.Error(
                message="Query Error",
                exception=qe,
                code=402
            )

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
