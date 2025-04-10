import time
import traceback
from aiohttp import web
from pandas import DataFrame
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
from ..outputs.tables import TableOutput
from ..queries.multi.operators import Filter, GroupBy
from ..conf import (
    CSV_DEFAULT_DELIMITER,
    CSV_DEFAULT_QUOTING
)

class QueryHandler(AbstractHandler):

    async def columns(self, request: web.Request) -> web.StreamResponse:
        raise self.no_content(
            headers={
                'Content-Type': 'application/json',
                'X-Message': 'No Columns available',
            }
        )

    async def query(self, request: web.Request) -> web.StreamResponse:
        total_time = 0
        started_at = time.monotonic()
        options = {}
        params = self.query_parameters(request)
        args = self.match_parameters(request)
        slug = args.get('slug', None)
        _format: str = 'json'
        meta = args.get('meta', None)
        writer_options = {}
        try:
            slug, _format = slug.split(':')
        except (ValueError, AttributeError):
            pass
        try:
            options = await self.json_data(request)
        except (TypeError, ValueError):
            options = {}
        # if option is None, then no JSON was sent:
        if options is None and slug is None:
            raise self.Error(
                reason="No JSON Data",
                message="No valid JSON data was not found in payload.",
                code=400
            )
        ## Getting data from Queries or Files
        if not slug:
            data = {}
            _queries = options.get('queries', {})
            _files = options.get('files', {})
            if not (_queries or _files):  # Check if both are effectively empty
                raise self.Error(
                    message='Invalid POST Option passed to MultiQuery.',
                    code=400
                )
        else:
            _queries = {}
            _files = {}
            data = options
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
        if _format == 'csv':
            try:
                writer_options = options['_csv_options']
                del options['_csv_options']
            except (TypeError, KeyError):  # default options:
                writer_options = {
                    "delimiter": CSV_DEFAULT_DELIMITER,
                    "quoting": CSV_DEFAULT_QUOTING
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
        ## will be a downloadable resource
        download = params.pop('_download', False)
        filename = params.pop('_filename', None)
        try:
            writer_options = options['_graph_options']
            del options['_graph_options']
        except (TypeError, KeyError):  # default options:
            pass
        output_args = {
            "filename": filename,
            "download": download,
            "writer_options": writer_options,
        }
        ## Step 1: Running all Queries and Files on QueryObject
        qs = MultiQS(
            slug=slug,
            queries=_queries,
            files=_files,
            query=options,
            conditions=data
        )
        try:
            result, options = await qs.query()
        except DataNotFound as dnf:
            return self.NoData(
                message=str(dnf),
                headers={
                    'Content-Type': 'application/json',
                    'X-Slug': slug,
                    'X-Format': queryformat,
                    'X-Total-Time': f'{total_time:.2f} seconds',
                    'X-Error': str(dnf),
                },
            )
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
            trace = traceback.format_exc()
            self.logger.exception(qe, stack_info=True)
            raise self.Error(
                message="Query Error",
                exception=qe,
                stacktrace=trace,
                code=402
            )
        except Exception as ex:
            trace = traceback.format_exc()
            self.logger.exception(ex, stack_info=True)
            raise self.Except(
                message=f"Unknown Error on Query: {ex!s}",
                exception=ex,
                stacktrace=trace,
            ) from ex

        ### Step 2: Check if result is empty or is a dictionary of dataframes:
        if result is None:
            raise self.Error(
                message="Empty Result",
                code=404
            )
        # Step 3: reduce to one single Dataframe:
        if isinstance(result, dict) and len(result) == 1:
            # TODO: making a melt or concat of all dataframes
            result = list(result.values())[0]
        ### Step 4: applying some Filter or GroupBy Transformations:
        # remove the grouping option from data, rest, is passed to filter:
        try:
            _grouping = data.pop('grouping', None)
        except (AttributeError, TypeError):
            _grouping = None
        if data:  # already have information to be passed to data
            _filter = {}
            try:
                ## making Join of Data
                _filter = data.pop('filter', {})
                if not _filter:
                    f = data.pop('where_cond', {})
                    if f:
                        _filter['filter'] = f
                if data is not None:
                    ft = {
                        "filter": {
                            **data
                        }
                    }
                    _filter = {**_filter, **ft}
                f = Filter(data=result, **_filter)
                result = await f.run()
            except (QueryException, Exception) as ex:
                raise self.Error(
                    message=f"Error on Filtering: {ex!s}",
                    exception=ex
                ) from ex
        if _grouping:
            try:
                ## Group By of Data:
                groupby = GroupBy(data=result, **_grouping)
                result = await groupby.run()
            except (QueryException, Exception) as ex:
                raise self.Error(
                    message=f"Error on GroupBy: {ex!s}",
                    exception=ex
                ) from ex
        ### Step 5: Passing result to TableOutput
        if isinstance(result, str):
            return self.response(
                result,
                headers={
                    'X-Slug': str(slug),
                    'X-Total-Time': f'{total_time:.2f} seconds',
                }
            )
        if isinstance(data, dict):
            if 'Output' in data:
                ## Optionally saving result into Database (using Pandas)
                for step in options['Output']:
                    obj = None
                    for step_name, component in step.items():
                        if step_name in ('tableOutput', 'TableOutput'):
                            obj = TableOutput(data=result, **component)
                            result = await obj.run()
        ### Step 6: passing Result to DataOutput
        try:
            if result is None or isinstance(result, DataFrame) and result.empty:
                raise DataNotFound(
                    message="Empty Result",
                    code=404
                )
            output = DataOutput(
                request,
                query=result,
                ctype=queryformat,
                slug=slug,
                **output_args
            )
            total_time = time.monotonic() - started_at
            self.logger.debug(
                f'Query Duration: {total_time:.2f} seconds'
            )
            return await output.response()
        except (DataNotFound) as ex:
            return self.NoData(
                message="No Data was Found",
                headers={
                    'Content-Type': 'application/json',
                    'X-Slug': slug,
                    'X-Format': queryformat,
                    'X-Total-Time': f'{total_time:.2f} seconds',
                    'X-Error': str(ex),
                },
            )
        except (DriverError) as err:
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
