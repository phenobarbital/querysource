from typing import Union
from aiohttp import web
from aiohttp.web_exceptions import HTTPInternalServerError, HTTPNoContent

from navconfig import DEBUG
from navconfig.logging import logging
from datamodel.parsers.encoders import DefaultEncoder
from asyncdb.exceptions import NoDataFound, StatementError, DriverError
from ..interfaces.queries import AbstractQuery
from ..exceptions import (
    DataNotFound,
    QueryException,
)
from ..utils.errors import build_error_payload
from .writers import (
    jsonWriter,
    CSVWriter,
    ExcelWriter,
    TXTWriter,
    HTMLWriter,
    BokehWriter,
    PlotlyWriter,
    TSVWriter,
    PickleWriter,
    TableWriter,
    # ProfileWriter,
    ReportWriter,
    PDFWriter,
    XMLWriter,
    # EDAWriter,
    # DescribeWriter,
    # ClusterWriter
)

WRITERS = {
    "json": jsonWriter,
    "table": TableWriter,
    "txt": TXTWriter,
    "plain": TXTWriter,
    "csv": CSVWriter,
    "tsv": TSVWriter,
    'excel': ExcelWriter,
    'xls': ExcelWriter,
    'xlsx': ExcelWriter,
    'xlsm': ExcelWriter,
    'ods': ExcelWriter,
    'html': HTMLWriter,
    'bokeh': BokehWriter,
    'plotly': PlotlyWriter,
    'pickle': PickleWriter,
    # 'profiling': ProfileWriter,
    'report': ReportWriter,
    'pdf': PDFWriter,
    'xml': XMLWriter,
    # 'eda': EDAWriter,
    # 'describe': DescribeWriter,
    # 'clustering': ClusterWriter
}

class DataOutput:
    """Main Router for Output formats.
    """

    def __init__(
        self,
        request: web.Request,
        query: Union[AbstractQuery, "DataFrame", list],
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
        try:
            self.filename = kwargs['filename'] or self.slug
        except KeyError:
            pass
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
            try:
                wt = WRITERS[self.format]
            except KeyError:
                ### invalid Writer, defaulting to json
                self.logger.warning(
                    f'Invalid Writer {self.format}, default to JSON.'
                )
                wt = WRITERS['json']
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
