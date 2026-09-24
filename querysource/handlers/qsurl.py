"""qsurl HTTP handler: GET /api/v1/services/qsurl/{path:.*} (FEAT-152)."""
from __future__ import annotations

import re

from aiohttp import web
from asyncdb.exceptions import ConnectionTimeout, ProviderError

from ..auth import ResourceType
from ..conf import CSV_DEFAULT_DELIMITER, CSV_DEFAULT_QUOTING
from ..exceptions import DriverError, ParserError, QueryException, SlugNotFound
from ..outputs import DataOutput
from ..qsurl import QSUrlError, parse
from ..qsurl.translate import split
from ..queries.qs import QS
from ..tenant_errors import TenantError
from ..tenants import QueryIdentity
from ..types import graph_ouputs, mime_supported
from .abstract import AbstractHandler

_BARE_SLUG = re.compile(r"^[A-Za-z0-9_-]+$")


class QSUrlService(AbstractHandler):
    """Parse a qsurl query, enforce PBAC and execute it through QS."""

    async def query(self, request: web.Request) -> web.StreamResponse:
        """Handle one qsurl read.

        Steps: decode-once source assembly, PBAC ``slug:execute``, output
        negotiation, capability probe + ``translate.split``, ``QS`` execution
        with the residual plan, ``datasource:use``/``driver:use`` PBAC, then
        ``DataOutput``.

        Raises:
            web.HTTPException: 400 (qsurl errors, with ``detail``), 404 (PBAC deny), others as QueryService.
        """
        params = self.query_parameters(request)
        args = self.match_parameters(request)
        try:
            options = await self.json_data(request) or {}
        except (TypeError, ValueError):
            options = {}
        try:
            source = self._source(args.pop("path", ""), params.pop("q", None))
            ir = parse(source)
            slug = ir["slug"]
            tenant = request.get("qs_tenant")
            await self._enforce_slug_execute(request, slug, tenant)
            queryformat, output_args = self._output_args(request, params, options)
            caps, scan = await self.resolve_capabilities(request, slug, tenant)
            pushdown, plan = split(ir, caps, residual_scan=scan)
            conditions = {**options, **params, **pushdown}
            self.logger.debug(
                f'qsurl Slug: {slug}, format: {queryformat}, conditions: {conditions}'
            )
            if query := await self.get_source(
                request, slug, conditions, driver=args, tenant=tenant,
                definition=request.get('qs_definition'), residual=plan,
            ):
                try:
                    await query.build_provider()
                except SlugNotFound as err:
                    raise self.Error(
                        message=f"Slug Not Found: {slug}",
                        exception=err,
                        code=400
                    ) from err
                except TenantError as err:
                    raise self.Error(
                        message=str(err),
                        exception=err,
                        code=err.code
                    ) from err
                except ParserError as err:
                    raise self.Error(
                        message=f"Error parsing Query Slug {slug}",
                        exception=err
                    ) from err
                except (ProviderError, DriverError) as err:
                    raise self.Error(
                        message="Connection Error",
                        exception=err
                    ) from err
                except Exception as ex:
                    raise self.Except(
                        message="Unknown Error on Query",
                        exception=ex
                    ) from ex
                # PBAC: enforce datasource:use and driver:use after provider is resolved.
                _ds_name = getattr(
                    getattr(getattr(query, '_qs', None), '_definition', None),
                    'provider',
                    'db'
                ) or 'db'
                _drv_name = getattr(
                    getattr(query, '_provider', None),
                    'driver',
                    'db'
                ) or 'db'
                await self._enforce_pbac(
                    request,
                    resource_type=ResourceType.DATASOURCE,
                    resource_name=_ds_name,
                    action="datasource:use",
                )
                await self._enforce_pbac(
                    request,
                    resource_type=ResourceType.DRIVER,
                    resource_name=_drv_name,
                    action="driver:use",
                )
                if not queryformat:
                    if ctype := query.accepts():
                        queryformat = mime_supported[ctype]
                try:
                    output = DataOutput(
                        request,
                        query=query,
                        ctype=queryformat,
                        slug=slug,
                        **output_args
                    )
                    return await output.response()
                except ConnectionTimeout as err:
                    raise DriverError(
                        f"Connection Timeout: {err}"
                    ) from err
                except (ProviderError, DriverError) as err:
                    raise self.Error(
                        message="DataOutput Error",
                        exception=err,
                        code=402
                    ) from err
                except QSUrlError:
                    raise
                except (QueryException, Exception) as ex:
                    raise self.Except(
                        message=f"Error on Query: {slug}",
                        exception=ex
                    ) from ex
            else:
                raise self.Error(
                    message=f"Unable to get Provider for slug: {slug}"
                )
        except QSUrlError as err:
            raise self.Error(message=err.message, exception=err, code=400, detail=err.to_dict()) from err
        except web.HTTPException:
            raise
        except (ProviderError, DriverError) as err:
            raise self.Error(
                message='Query Failed',
                exception=err
            ) from err
        except (QueryException, Exception) as ex:
            raise self.Except(
                message="Uncaught Error on Query",
                exception=ex
            ) from ex

    @staticmethod
    def _source(path: str, q: str | None) -> str:
        """Return the qsurl source; never re-decodes (aiohttp decoded once)."""
        if q is None:
            return path
        if not _BARE_SLUG.match(path):
            raise QSUrlError("parse", "with ?q= the path must be a bare slug; q carries everything after it")
        return f"{path}{q}"

    async def _enforce_slug_execute(self, request: web.Request, slug: str, tenant: str | None) -> None:
        """PBAC slug:execute, tenant-aware — verbatim logic of service.py:200-225."""
        registry = request.app.get("qs_tenant_registry")
        if registry is not None:
            try:
                store = registry.resolve(tenant)
            except web.HTTPNotFound:
                raise
            except Exception as exc:  # pylint: disable=W0703
                self.logger.warning(
                    "QSUrlService ownership pre-flight error (fail-closed): %s",
                    exc,
                )
                raise web.HTTPNotFound() from exc
            identity = QueryIdentity(store=store, slug=slug)
            await self._enforce_owned_slug(
                request,
                identity=identity,
                action="slug:execute",
            )
        else:
            await self._enforce_pbac(
                request,
                resource_type=ResourceType.SLUG,
                resource_name=slug,
                action="slug:execute",
            )

    def _output_args(self, request: web.Request, params: dict, options: dict) -> tuple[str | None, dict]:
        """queryformat + DataOutput kwargs — verbatim logic of service.py:227-289 (pops its keys from params/options)."""
        _format: str | None = None
        writer_options: dict = {}
        try:
            _format = params['queryformat']
            del params['queryformat']
        except KeyError:
            pass
        try:
            _format, tpl = _format.split('=')
        except (AttributeError, ValueError):
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
        queryformat = self.format(request, params, _format)
        try:
            download = params['_download']
            del params['_download']
        except KeyError:
            download = False
        try:
            filename = params['_filename']
            del params['_filename']
        except KeyError:
            filename = None
        if _format == 'csv':
            try:
                writer_options = options['_csv_options']
                del options['_csv_options']
            except (TypeError, KeyError):
                writer_options = {
                    "delimiter": CSV_DEFAULT_DELIMITER,
                    "quoting": CSV_DEFAULT_QUOTING
                }
        else:
            try:
                writer_options = options['_output_options']
                del options['_output_options']
            except (TypeError, KeyError):
                pass
            try:
                del options['_csv_options']
            except (TypeError, KeyError):
                pass
        if queryformat in graph_ouputs:
            writer_options = {}
        try:
            writer_options = options['_graph_options']
            del options['_graph_options']
        except (TypeError, KeyError):
            pass
        output_args = {
            "filename": filename,
            "download": download,
            "writer_options": writer_options,
        }
        return queryformat, output_args

    async def resolve_capabilities(self, request: web.Request, slug: str, tenant: str | None) -> tuple[frozenset[str], bool]:
        """Resolve (capabilities, residual_scan) of the provider that will execute ``slug`` via a lazy probe QS."""
        probe = QS(slug=slug, conditions={}, request=request, tenant=tenant,
                   definition=request.get("qs_definition"), lazy=True)
        try:
            await probe.build_provider()
            return probe._qs.capabilities, probe._qs.residual_scan  # pylint: disable=protected-access
        except SlugNotFound as err:
            raise self.Error(
                message=f"Slug Not Found: {slug}",
                exception=err,
                code=400
            ) from err
        except TenantError as err:
            raise self.Error(
                message=str(err),
                exception=err,
                code=err.code
            ) from err
        finally:
            try:
                await probe.close()
            except Exception:  # pylint: disable=W0703
                pass
