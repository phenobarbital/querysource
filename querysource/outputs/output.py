from importlib import import_module
from typing import Optional, Union

from aiohttp import web
from aiohttp.web_exceptions import HTTPInternalServerError, HTTPNoContent
from asyncdb.exceptions import DriverError, NoDataFound, StatementError
from datamodel.parsers.encoders import DefaultEncoder
from navconfig import DEBUG
from navconfig.logging import logging
from pandas import DataFrame

from ..exceptions import (
    DataNotFound,
    QueryException,
)
from ..interfaces.queries import AbstractQuery
from ..ownership_logging import implicit_artifact_name
from ..qsurl.errors import QSUrlError
from ..utils.errors import build_error_payload
from .writers.abstract import AbstractWriter

_WRITERS_PACKAGE = "querysource.outputs.writers"
_logger = logging.getLogger('QS.Output')


class LazyWriterRegistry(dict):
    """Format → writer registry; stored values are specs or writer classes.

    Reads (``[]`` and ``get``) always return a class: a ``str`` spec is imported
    from ``querysource.outputs.writers.<submodule>`` on first access and cached
    back with ``dict.__setitem__``. Writes, ``monkeypatch.setitem``, ``in`` and
    ``len`` behave like a plain ``dict``; ``values()``/``items()`` expose the
    stored form (spec or class).
    """

    def __getitem__(self, ctype: str) -> type[AbstractWriter]:
        """Resolve and return the writer class registered for ``ctype``.

        Args:
            ctype: output format key (``"json"``, ``"pdf"``, ...).

        Returns:
            type[AbstractWriter]: the writer class (or an injected class as-is).

        Raises:
            KeyError: ``ctype`` is not registered.
            ImportError: the registered spec cannot be imported or names a
                missing class (raised ``from`` the original error).
        """
        value = dict.__getitem__(self, ctype)
        if not isinstance(value, str):
            return value
        try:
            submodule, class_name = value.split(":", 1)
            module = import_module(f".{submodule}", _WRITERS_PACKAGE)
            cls = getattr(module, class_name)
        except Exception as exc:
            raise ImportError(
                f"cannot load writer {ctype!r} from {value!r}: {exc}"
            ) from exc
        dict.__setitem__(self, ctype, cls)
        return cls

    def get(
        self, ctype: str, default: Optional[type[AbstractWriter]] = None
    ) -> Optional[type[AbstractWriter]]:
        """Like ``__getitem__`` but return ``default`` for a missing key.

        Raises:
            ImportError: a registered spec fails to resolve (never masked as
                ``default``).
        """
        if ctype not in self:
            return default
        return self[ctype]


WRITERS: LazyWriterRegistry = LazyWriterRegistry({
    "json": "json:jsonWriter",
    "table": "table:TableWriter",
    "txt": "txt:TXTWriter",
    "plain": "txt:TXTWriter",
    "csv": "csv:CSVWriter",
    "tsv": "tsv:TSVWriter",
    'excel': "excel:ExcelWriter",
    'xls': "excel:ExcelWriter",
    'xlsx': "excel:ExcelWriter",
    'xlsm': "excel:ExcelWriter",
    'ods': "excel:ExcelWriter",
    'html': "html:HTMLWriter",
    'bokeh': "bokeh:BokehWriter",
    'plotly': "plotly:PlotlyWriter",
    'pickle': "pickle:PickleWriter",
    # 'profiling': "profiling:ProfileWriter",
    'report': "report:ReportWriter",
    'pdf': "pdf:PDFWriter",
    'xml': "xml:XMLWriter",
    # 'eda': "eda:EDAWriter",
    # 'describe': "describe:DescribeWriter",
    # 'clustering': "clustering:ClusterWriter"
})


def resolve_writer(ctype: str) -> type[AbstractWriter]:
    """Return the writer class for ``ctype``, importing it on first use.

    A format that is not registered logs a warning and falls back to the
    json writer (unchanged behaviour). A registered format always returns
    its own class — the lazily-imported one or an injected override.

    Args:
        ctype: output format key.

    Returns:
        type[AbstractWriter]: the writer class to instantiate.

    Raises:
        ImportError: a registered writer fails to import; never swallowed
            into the json fallback.
    """
    if ctype not in WRITERS:
        _logger.warning(f'Invalid Writer {ctype}, default to JSON.')
        return WRITERS['json']
    return WRITERS[ctype]

class DataOutput:
    """Main Router for Output formats.
    """

    def __init__(
        self,
        request: web.Request,
        query: Union[AbstractQuery, DataFrame, list],
        ctype: str = 'json',
        slug: str = None,
        **kwargs
    ) -> None:
        self.request = request
        compression = None
        self.query = None
        self.logger = logging.getLogger('QS.Output')
        # determine content negotiation
        if compression := request.headers.get('X-Encoding', None):
            self._compression = compression
        elif compression := request.headers.get('Accept-Encoding', None):
            self._compression = compression
        else:
            self._compression = None
        try:
            if ',' in self._compression:
                self._compression = self._compression.split(',')[0]
        except (TypeError, AttributeError, KeyError):
            self._compression = None
        if self._compression not in ('gzip', 'deflate'):
            self._compression = None
            self.response_type = 'web'
        else:
            self.response_type = 'stream'
        host = request.headers.get('HOST', None)
        self.logger.debug(
            f'QuerySource Output: host: {host!s} compression: {compression!s} status: {self._compression!s}'
        )
        self.query = query
        self.format = ctype
        self.columns = []
        self.slug = slug
        self.filename = self.slug
        ## encoder:
        self._json = DefaultEncoder()
        ### get name of the file:
        explicit_filename = False
        try:
            self.filename = kwargs['filename'] or self.slug
            explicit_filename = bool(kwargs['filename'])
        except KeyError:
            pass
        # Implicit tenant artifact naming (TASK-731 AC-1/AC-3): only ever
        # applied when the caller did NOT explicitly configure a filename —
        # an explicit filename/S3 key/table identifier is never prefixed.
        # Ownership comes from the executed definition on `query` itself
        # (never a mutable URL field); a raw DataFrame/list `query` has no
        # identity, so the filename is left unchanged.
        if not explicit_filename and self.filename:
            identity = getattr(query, '_definition_identity', None)
            execution_id = getattr(query, '_execution_id', None)
            self.filename = implicit_artifact_name(
                identity, execution_id, self.filename
            )
        try:
            self.download = kwargs['download']
        except KeyError:
            self.download: bool = False
        try:
            self.writer_options: dict = kwargs['writer_options']
        except KeyError:
            self.writer_options: dict = {}

    def error(
        self,
        message: str,
        status: int = 400,
        exception: BaseException = None,
        headers: dict = None,
        content_type: str = 'application/json'
    ) -> BaseException:
        """Build a client-safe error payload and raise the appropriate aiohttp exception.

        Delegates body construction to ``build_error_payload``, which logs full
        detail (message + traceback) server-side and returns a minimal payload in
        production (``DEBUG=False``) or a verbose one in development.

        Note: This method always RAISES (never returns) — callers use the
        ``return self.error(...)`` idiom to satisfy linters, but execution never
        reaches the ``return`` statement.
        """
        # Map HTTP status to a formatter category
        if status == 404:
            category = "not_found"
        elif status >= 500:
            category = "server_error"
        else:
            category = "query_error"

        payload = build_error_payload(
            category=category,
            status=status,
            exception=exception,
            debug=DEBUG,
            logger=self.logger,
            public_message=message if DEBUG else None,
        )
        args = {
            "text": self._json.dumps(payload),
            "content_type": content_type,
            "headers": {
                "X-MESSAGE": payload["error"],
                "X-STATUS": str(status),
            }
        }
        if status == 400:
            obj = web.HTTPBadRequest(**args)
        elif status == 401:
            obj = web.HTTPUnauthorized(**args)
        elif status == 403:  # forbidden
            obj = web.HTTPForbidden(**args)
        elif status == 404:  # not found
            obj = web.HTTPNotFound(**args)
        elif status == 406:  # Not acceptable
            obj = web.HTTPNotAcceptable(**args)
        elif status == 412:
            obj = web.HTTPPreconditionFailed(**args)
        elif status == 428:
            obj = web.HTTPPreconditionRequired(**args)
        elif status >= 500:
            obj = HTTPInternalServerError(**args)
        else:
            obj = web.HTTPBadRequest(**args)
        if headers:
            for header, value in headers.items():
                obj.headers[header] = str(value)
        raise obj

    def no_content(self, headers: dict = None, content_type: str = 'application/json') -> web.Response:
        response = HTTPNoContent(
            content_type=content_type
        )
        response.headers["Pragma"] = "no-cache"
        if headers:
            for header, value in headers.items():
                response.headers[header] = str(value)
        return response

    async def response(self):
        if self.query is not None:
            self.logger.debug(
                f'::: SENDING RESPONSE in format: {self.format!s}'
            )
            ### before, making calculation of stats.
            wt = resolve_writer(self.format)
            writer = wt(
                request=self.request,
                resultset=self.query,
                filename=self.filename,
                response_type=self.response_type,
                download=self.download,
                compression=self._compression,
                ctype=self.format,
                **self.writer_options
            )
            ### Return data on Output:
            try:
                await writer.get_result()
            except QSUrlError:
                raise  # FEAT-152: the qsurl handler answers 400 with the structured detail
            except (NoDataFound, DataNotFound) as err:
                _msg = f"{err!s}" if DEBUG else "Data not found"
                headers = {
                    'x-status': 'Empty Result',
                    'x-message': _msg
                }
                return self.no_content(
                    headers=headers
                )
            except StatementError as err:
                _msg = f"{err!s}" if DEBUG else "Query Syntax Error"
                headers = {
                    'x-status': 'Syntax Error',
                    'x-message': _msg
                }
                return self.error(
                    f"Query Syntax Error: {err}",
                    status=404,
                    exception=err,
                    headers=headers,
                    content_type='application/json'
                )
            except (DriverError, QueryException) as err:
                _msg = f"{err!s}" if DEBUG else "Query execution failed"
                headers = {
                    'x-status': 'Query Error',
                    'x-message': _msg
                }
                return self.error(
                    f"Query Error: {err}",
                    status=400,
                    exception=err,
                    headers=headers,
                    content_type='application/json'
                )
            except Exception as err:  # pylint: disable=W0703
                return self.error(  # pylint: disable=E0702
                    message=f"Query Exception: {err}",
                    status=500,
                    exception=err,
                    content_type='application/json'
                )
            try:
                return await writer.get_response()
            except (TypeError, RuntimeError, ValueError) as err:
                _msg = f'Writer Error: {err}' if DEBUG else "Output generation failed"
                headers = {
                    'x-status': 'Output Error',
                    'x-message': _msg
                }
                return self.error(
                    f"Output Error: {err}",
                    status=400,
                    exception=err,
                    headers=headers,
                    content_type='application/json'
                )
            except Exception as err:  # pylint: disable=W0703
                _msg = f'Writer Error: {err}' if DEBUG else "Output generation failed"
                headers = {
                    'x-status': 'QuerySource Error',
                    'x-message': _msg
                }
                return self.error(
                    "Output Exception",
                    status=500,
                    exception=err,
                    headers=headers,
                    content_type='application/json'
                )
        else:
            return self.error(
                message="Query Object was not found",
                headers={
                    'x-status': 'Error: Missing Query',
                    'x-message': 'Query Object was not found'
                },
                content_type='application/json'
            )
