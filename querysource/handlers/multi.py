import json
import time
import traceback
from datetime import datetime

from aiohttp import web
from pandas import DataFrame

from ..auth import ResourceType
from ..conf import CSV_DEFAULT_DELIMITER, CSV_DEFAULT_QUOTING
from ..exceptions import (
    DataNotFound,
    DriverError,
    OutputError,
    ParserError,
    QueryException,
    SlugNotFound,
)
from ..outputs import DataOutput
from ..queries import MultiQS
from ..queries.multi.operators import Filter, GroupBy
from ..tenant_errors import TenantError
from ..tenants import QueryIdentity
from .abstract import AbstractHandler


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

    async def _preflight_multiquery_owned(
        self,
        request: web.Request,
        slugs: list,
        files: list,
        has_raw_query: bool,
    ) -> None:
        """Check real resolved QueryIdentity objects before executing batches.

        This preflight checks actual saved child slugs, not output aliases.
        It resolves each slug to its QueryIdentity and enforces ownership
        using a detached evaluator with cleared cache for tenant isolation.

        Files and raw actions preserve existing behavior (no ownership check).

        Args:
            request: The current aiohttp web request.
            slugs: List of slug/query names to check with slug:execute.
            files: List of file keys (not checked - existing behavior).
            has_raw_query: True if raw query present (not checked - existing behavior).

        Raises:
            web.HTTPNotFound: When any slug ownership check fails.
        """
        # Files and raw queries use existing behavior (no ownership check)
        if not slugs:
            return

        # PBAC disabled check
        if request.app.get("security") is None:
            return

        # Same source of truth execution uses: TenantQueryHandler.query()
        # stashes the resolved (path-based) tenant selector on
        # request['qs_tenant'] before delegating here — NOT the query
        # string, which the tenant route never populates (the selector
        # lives in the URL path). Reading request.query.get("tenant")
        # here would silently preflight against the default/legacy store
        # while MultiQS then executes with the real path tenant — an
        # authz/execution mismatch.
        tenant = request.get('qs_tenant')

        # Get the registry from the app. Published as app["qs_tenant_registry"]
        # by QuerySource.qs_start (TASK-720) — verified against
        # querysource/services.py. A missing key means this app has not
        # adopted the tenant feature at all (same "legacy, not wired up"
        # case TASK-723/724 handle for the management handlers): skip this
        # tenant-specific check and rely on the existing alias-based
        # _preflight_multiquery PBAC check above, which still applies.
        registry = request.app.get("qs_tenant_registry")
        if registry is None:
            return

        # Once the tenant feature IS active for this app, a resolution
        # failure must fail closed (AC-1 "fail-closed errors"), matching
        # the sibling _preflight_multiquery's own
        # "except Exception ... raise web.HTTPNotFound()" convention just
        # above — never silently allow an unverifiable batch through.
        try:
            store = registry.resolve(tenant)
        except web.HTTPNotFound:
            raise
        except Exception as exc:
            self.logger.warning(
                "MultiQuery ownership pre-flight error (fail-closed): %s", exc
            )
            raise web.HTTPNotFound() from exc

        # Check each slug for ownership
        for slug in slugs:
            identity = QueryIdentity(store=store, slug=slug)
            try:
                await self._enforce_owned_slug(
                    request,
                    identity=identity,
                    action="slug:execute",
                )
            except web.HTTPNotFound:
                self.logger.info(
                    "MultiQuery ownership denied: slug=%s tenant=%s",
                    slug,
                    tenant or "default",
                )
                raise

    async def columns(self, request: web.Request) -> web.StreamResponse:
        """Column inspection for a stored multi definition.

        Uses ``request['qs_definition'].runtime.columns_definition`` when the tenant
        dispatcher stashed a definition. HEAD answers 204 with ``X-Columns``/``X-Slug``
        (plus ``X-Message: No Columns found`` when empty). PATCH answers 200 with the
        list, or the legacy 204 ``No Columns available`` when empty. Without a
        definition the legacy 204 is returned unchanged (v3 callers).
        """
        definition = request.get('qs_definition')
        columns = list(getattr(definition.runtime, 'columns_definition', None) or []) if definition else []
        if definition is not None and request.method == 'HEAD':
            headers = {
                'Content-Type': 'application/json',
                'X-Columns': f"{columns!r}",
                'X-Slug': str(definition.identity.slug),
            }
            if not columns:
                headers['X-Message'] = 'No Columns found'
            return self.no_content(headers=headers)
        if columns:
            return self.json_response(columns, status=200)
        raise self.no_content(
            headers={
                'Content-Type': 'application/json',
                'X-Message': 'No Columns available',
            }
        )

    async def test_slug(self, request: web.Request) -> web.StreamResponse:
        """Validate a stored multi definition without executing it (FEAT-151).

        Requires ``request['qs_definition']`` (set by TenantQueryHandler). Resolves each
        saved child's owner with ``MultiQS.resolve_child_owner``, checks existence with
        the definition repository and ownership with ``_enforce_owned_slug``, and returns
        the single dry-run envelope extended with ``kind``, ``children``, ``files``,
        ``sources`` and ``warnings``. Never builds executors, opens datasource
        connections, or runs EXPLAIN.
        """
        started = datetime.now()
        definition = request.get('qs_definition')
        if definition is None:
            return self.error(response={'message': 'No stored definition to test.'}, status=400)
        params = self.query_parameters(request) or {}
        ignore_query = bool(params.pop('ignore_query', False))
        args = self.match_parameters(request) or {}
        _slug_arg = args.get('slug')
        _format = 'json'
        if isinstance(_slug_arg, str) and ':' in _slug_arg:
            try:
                _, _format = _slug_arg.split(':')
            except ValueError:
                pass
        try:
            queryformat = self.format(request, params, _format)
        except ValueError:
            queryformat = 'json'
        tenant = request.get('qs_tenant')
        registry = request.app.get('qs_tenant_registry')
        repo = request.app.get('qs_definition_repository')
        security = request.app.get('security')
        warnings: list = []
        payload: dict = {}

        raw = getattr(definition.runtime, 'query_raw', None) or ''
        parsed = None
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = None
        if isinstance(parsed, dict) and (
            'queries' in parsed or 'files' in parsed or 'sources' in parsed
        ):
            payload = parsed
        else:
            warnings.append(
                "query_raw is not a multi-query payload; MultiQS will fall back to "
                "single-query mode"
            )
            payload = {}

        children: list = []
        for alias, cfg in (payload.get('queries') or {}).items():
            if not isinstance(cfg, dict):
                cfg = {}
            child_slug = cfg.get('slug')
            if not child_slug:
                # Raw inline query child ('query' key, no stored slug) — no
                # definition to own-check (S5, same convention as the
                # execution preflight's files/raw handling).
                children.append({
                    'alias': alias,
                    'slug': None,
                    'kind': 'raw',
                    'tenant': None,
                    'store': None,
                    'exists': None,
                    'allowed': None,
                    'error': None,
                })
                continue
            child_tenant, store = MultiQS.resolve_child_owner(cfg, tenant, registry)
            store_repr = f"{store.schema}.{store.table}" if store is not None else None
            identity = QueryIdentity(store=store, slug=child_slug)
            exists = True
            error = None
            try:
                await repo.get(identity)
            except TenantError as terr:
                exists = False
                error = terr.error_code
            allowed = None
            if security is not None:
                try:
                    await self._enforce_owned_slug(
                        request, identity=identity, action='slug:execute'
                    )
                    allowed = True
                except web.HTTPNotFound:
                    allowed = False
            children.append({
                'alias': alias,
                'slug': child_slug,
                'kind': 'slug',
                'tenant': child_tenant,
                'store': store_repr,
                'exists': exists,
                'allowed': allowed,
                'error': error,
            })

        works = all(c['exists'] is not False and c['allowed'] is not False for c in children)

        sources: list = []
        for entry in MultiQS._normalize_sources(payload.get('sources', [])):
            if isinstance(entry, dict):
                sources.extend(entry.keys())

        resultset = {
            'slug': definition.identity.slug,
            'kind': 'multi',
            'works': works,
            'error': None,
            'generated': (datetime.now() - started).total_seconds(),
            'execution': None,
            'tenant': tenant,
            'store': f"{definition.identity.store.schema}.{definition.identity.store.table}",
            'children': children,
            'files': sorted((payload.get('files') or {}).keys()),
            'sources': sources,
            'warnings': warnings,
        }
        if not ignore_query:
            resultset['conditions'] = params
            resultset['query'] = payload

        if queryformat in ('txt', 'plain', 'raw'):
            return self.response(
                response=json.dumps(payload, indent=2),
                content_type='text/plain'
            )
        return self.json_response(resultset, status=200)

    async def query(self, request: web.Request) -> web.StreamResponse:
        total_time = 0
        started_at = time.monotonic()
        options = {}
        params = self.query_parameters(request)
        args = self.match_parameters(request)
        slug = args.get('slug', None)
        _format: str = 'json'
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
        # Step 1b: Ownership preflight for tenant isolation.
        # Real stored slugs — the alias keys of `_queries` (the output
        # label each child result is keyed by) are NOT a stand-in for the
        # actual saved definition being referenced (AC-1 "check actual
        # saved child slugs, not output aliases"). A raw-SQL child (no
        # `slug` key) has no stored definition to own-check at all —
        # skipped here exactly like `_preflight_multiquery`'s own existing
        # "files/raw actions preserve existing behavior" convention.
        _owned_slugs = [
            cfg["slug"]
            for cfg in (_queries or {}).values()
            if isinstance(cfg, dict) and cfg.get("slug")
        ]
        await self._preflight_multiquery_owned(
            request,
            slugs=_owned_slugs,
            files=list((_files or {}).keys()),
            has_raw_query=_has_raw,
        )
        _user_session = request.get('user_session')  # memoized by _get_user_session above

        # Owner-aware execution: TenantQueryHandler.query() stashes the
        # resolved tenant selector on request['qs_tenant'] before
        # delegating here (querysource/handlers/tenant.py); legacy v2/v3
        # callers never set it, so tenant stays None (MultiQS's own
        # default — AC-3, unchanged behavior for every non-tenant route).
        _tenant = request.get('qs_tenant')

        ## Step 1b: Running all Queries and Files on QueryObject
        qs = MultiQS(
            slug=slug,
            queries=_queries,
            files=_files,
            query=options,
            conditions=data,
            user_session=_user_session,
            tenant=_tenant,
            definition=request.get('qs_definition'),
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
        except TenantError as err:
            # Placed BEFORE the broader (QueryException, DriverError) branch
            # since TenantError IS a QueryException subclass — without this,
            # every ownership error (query_not_found=404,
            # tenant_store_unavailable=503, tenant_write_forbidden=403, ...)
            # was silently collapsed to the generic code=402 below. Preserve
            # the error's own stable machine code (spec §"New ownership
            # errors": "use the current error envelope for new codes").
            trace = traceback.format_exc()
            _remote_queries_on_err = getattr(qs, '_remote_queries', [])
            if _remote_queries_on_err:
                self.logger.warning(
                    "MultiQuery ownership error after remote queries %s: %s",
                    _remote_queries_on_err,
                    err,
                )
            raise self.Error(
                message=str(err),
                exception=err,
                stacktrace=trace,
                code=err.code
            )
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
