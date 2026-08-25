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
    OutputError,
)
from .abstract import AbstractHandler
from ..queries import MultiQS
from ..queries.multi.operators import Filter, GroupBy
from ..conf import (
    CSV_DEFAULT_DELIMITER,
    CSV_DEFAULT_QUOTING
)
from ..auth import ResourceType

class QueryHandler(AbstractHandler):

    async def _preflight_multiquery(
        self,
        request: web.Request,
        slugs: list,
        files: list,
        has_raw_query: bool,
    ) -> None:
        """All-or-nothing PBAC pre-flight for MultiQuery.

        Calls Guardian.filter_resources() once per non-empty resource-type
        bucket. If any one component is denied, raises web.HTTPNotFound
        immediately — the entire MultiQuery is rejected.

        Args:
            request: The current aiohttp web request.
            slugs: List of slug/query names to check with slug:execute.
            files: List of file keys to check with slug:execute (files are
                treated as named-query resources).
            has_raw_query: True if the payload contains any raw inline query;
                triggers a single raw_query:execute check.

        Raises:
            web.HTTPNotFound: When any component is denied, or when the
                user session is absent.
        """
        guardian = request.app.get('security')
        if guardian is None:
            return  # PBAC disabled — fast-path no-op

        session = await self._get_user_session(request)
        if session is None:
            self.logger.info("MultiQuery PBAC denied: no user session")
            raise web.HTTPNotFound()

        try:
            if slugs:
                r = await guardian.filter_resources(
                    resources=slugs,
                    request=request,
                    resource_type=ResourceType.SLUG,
                    action="slug:execute",
                )
                if r.denied:
                    self.logger.info(
                        "MultiQuery PBAC denied: slugs=%s", r.denied
                    )
                    raise web.HTTPNotFound()

            if files:
                r = await guardian.filter_resources(
                    resources=files,
                    request=request,
                    resource_type=ResourceType.SLUG,
                    action="slug:execute",
                )
                if r.denied:
                    self.logger.info(
                        "MultiQuery PBAC denied: files=%s", r.denied
                    )
                    raise web.HTTPNotFound()

            if has_raw_query:
                await self._enforce_pbac(
                    request,
                    resource_type=ResourceType.RAW_QUERY,
                    resource_name="raw_query",
                    action="raw_query:execute",
                )
        except web.HTTPNotFound:
            raise  # already the correct exception
        except Exception as exc:  # Guardian internal errors (AccessDenied, etc.)
            self.logger.warning(
                "MultiQuery PBAC pre-flight error (fail-closed): %s", exc
            )
            raise web.HTTPNotFound() from exc

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
        elif options is None:
            options = {}
        # if no return, then we don't need to return anything:
        self.no_return: bool = options.pop('no_return', False)
        ## Getting data from Queries or Files
        if not slug:
            data = {}
            _queries = options.get('queries', {})
            _files = options.get('files', {})
            _sources = options.get('sources', {})
            if not (_queries or _files or _sources):  # Check if all inputs are effectively empty
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
        ## Step 1: PBAC pre-flight (all-or-nothing) before thread fan-out.
        # Detect raw inline query: any non-slug, non-file/source query component.
        _has_raw = bool(
            not slug and not _queries and not _files and not _sources
        ) or bool(
            isinstance(options, dict) and options.get('query') and
            not _queries and not _files and not slug
        )
        await self._preflight_multiquery(
            request,
            slugs=list((_queries or {}).keys()),
            files=list((_files or {}).keys()),
            has_raw_query=_has_raw,
        )
        _user_session = request.get('user_session')  # memoized by _get_user_session above

        ## Step 1b: Running all Queries and Files on QueryObject
        qs = MultiQS(
            slug=slug,
            queries=_queries,
            files=_files,
            query=options,
            conditions=data,
            user_session=_user_session,
        )
        try:
            result, options = await qs.query()
        except DataNotFound as dnf:
            total_time = time.monotonic() - started_at
            _remote_queries_on_err = getattr(qs, '_remote_queries', [])
            if _remote_queries_on_err:
                self.logger.warning(
                    "MultiQuery DataNotFound after remote queries %s: %s",
                    _remote_queries_on_err,
                    dnf,
                )
            _err_headers = {
                'Content-Type': 'application/json',
                'X-Slug': slug,
                'X-Format': queryformat,
                'X-Total-Time': f'{total_time:.2f} seconds',
                'X-Error': str(dnf),
            }
            if _remote_queries_on_err:
                _err_headers['X-Remote-Queries'] = ','.join(_remote_queries_on_err)
            return self.NoData(
                message=str(dnf),
                headers=_err_headers,
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
        except OutputError as oe:
            # FEAT-146: MultiQS is the single authoritative Output executor;
            # a raised OutputError means a destination failed. Map its
            # category to a differentiated HTTP status: data -> 422 (via
            # self.Error, a client/query_error category) and infra/unknown
            # -> 500 (via self.Except, the existing server-error responder —
            # self.Error() has no code=500 branch and would otherwise fall
            # through to 400). Both routes call build_error_payload, which
            # exposes the real OutputError detail in the body regardless of
            # `debug` (TASK-712). Placed BEFORE the broader
            # (QueryException, DriverError) branch since OutputError is
            # itself a QueryException subclass.
            trace = traceback.format_exc()
            self.logger.exception(oe, stack_info=True)
            # step_name is attacker-influenced (it's the Output step's dict
            # key straight from the request body — e.g. an unregistered
            # destination name echoed back by get_destination()'s error), so
            # it must be sanitized the same way header_detail is below.
            step = " ".join(
                str(getattr(oe, "step_name", None) or "Output").splitlines()
            )
            if getattr(oe, "category", None) == "data":
                err = self.Error(
                    message=str(oe),
                    exception=oe,
                    stacktrace=trace,
                    code=422,
                )
            else:
                err = self.Except(
                    message=str(oe),
                    exception=oe,
                    stacktrace=trace,
                    code=500,
                )
            # X-Output-Errors retained as supplementary detail (not the only
            # signal anymore — the status code + body now carry the failure).
            # HTTP header values may not contain embedded CR/LF (aiohttp
            # raises on write) — destination messages (e.g. TableOutput's
            # multi-line "Unconsumed column names" text) are collapsed to a
            # single line here; the full detail is still in the body.
            header_detail = " ".join(str(oe).splitlines())
            err.headers['X-Output-Errors'] = f"{step}: {header_detail}"
            raise err
        except (QueryException, DriverError) as qe:
            trace = traceback.format_exc()
            _remote_queries_on_err = getattr(qs, '_remote_queries', [])
            if _remote_queries_on_err:
                self.logger.warning(
                    "MultiQuery error after remote queries %s: %s",
                    _remote_queries_on_err,
                    qe,
                )
            self.logger.exception(qe, stack_info=True)
            raise self.Error(
                message="Query Error",
                exception=qe,
                stacktrace=trace,
                code=402
            )
        except Exception as ex:
            trace = traceback.format_exc()
            _remote_queries_on_err = getattr(qs, '_remote_queries', [])
            if _remote_queries_on_err:
                self.logger.warning(
                    "MultiQuery unexpected error after remote queries %s: %s",
                    _remote_queries_on_err,
                    ex,
                )
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
        # MultiQuery-specific keys are processed by MultiQS (or come from a
        # saved slug echoed back as body) and must NOT be treated as inline
        # filter conditions.
        if isinstance(data, dict):
            for _mq_key in (
                'queries', 'files',
                'Info', 'Join', 'Concat', 'Melt', 'Merge',
                'Transform', 'Filter', 'GroupBy', 'Output', 'Processors',
            ):
                data.pop(_mq_key, None)
        if data:  # already have information to be passed to data
            _filter = {}
            try:
                ## making Join of Data
                _filter = data.pop('filter', {})
                if not _filter:
                    f = data.pop('where_cond', {})
                    if f:
                        _filter['filter'] = f
                if data:
                    ft = {
                        "filter": {
                            **data
                        }
                    }
                    _filter = {**_filter, **ft}
                if _filter:
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
        # FEAT-146: Output is now executed exactly once, solely by
        # MultiQS.query() above (single authoritative Output executor). A
        # failed destination raises OutputError there and is mapped to a
        # 422/500 response in the `except OutputError` branch — there is no
        # longer a duplicate Output loop here.
        ### Step 6: passing Result to DataOutput
        try:
            if result is None or isinstance(result, DataFrame) and result.empty:
                raise DataNotFound(
                    message="Empty Result",
                    code=404
                )
            if self.no_return:
                return self.response(
                    headers={
                        'X-Total-Time': f'{total_time:.2f} seconds',
                    },
                    status=204
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
            response = await output.response()
            # Add X-Remote-Queries header if any queries ran remotely (FEAT-101).
            remote_queries = getattr(qs, '_remote_queries', [])
            if remote_queries:
                response.headers['X-Remote-Queries'] = ','.join(remote_queries)
            return response
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
