import traceback

from aiohttp import web
from aiohttp.web_exceptions import HTTPInternalServerError, HTTPNoContent
from navconfig.logging import logging
from asyncdb.exceptions import NoDataFound, StatementError, ProviderError, DriverError
from querysource.libs.encoders import DefaultEncoder
from querysource.queries.abstract import BaseQuery
from querysource.exceptions import (
    DataNotFound,
    QueryException,
)
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
    # 'eda': EDAWriter,
    # 'describe': DescribeWriter,
    # 'clustering': ClusterWriter
}

class DataOutput:
    """Main Router for Output formats.
    """

    def __init__(self, request: web.Request, query: BaseQuery, ctype: str = 'json', slug : str = None, **kwargs):
        self.request = request
        compression = None
        self.query = None
        # determine content negotiation
        if compression := request.headers.get('X-Encoding', None):
            self._compression = compression
        elif compression:= request.headers.get('Accept-Encoding', None):
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
        logging.debug(
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
            self.filename = kwargs['filename']
            if not self.filename:
                self.filename = self.slug
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
        trace = None
        message = f"{message}: {exception!s}"
        if exception:
            trace = traceback.format_exc(limit=10)
        reason = {
            "error": message,
            "trace": self._json.dumps(trace)
        }
        args = {
            "reason": reason,
            "headers": {
                "content_type": content_type,
                "X-MESSAGE": str(message).replace('\n', ', '),
                "X-STATUS": str(status).replace('\n', ', ')
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
        elif status == 406: # Not acceptable
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
                obj.headers[header] = value
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
            logging.debug(f'::: SENDING RESPONSE in format: {self.format!s}')
            ### before, making calculation of stats.
            try:
                wt = WRITERS[self.format]
            except KeyError:
                ### invalid Writer, defaulting to json
                logging.warning(f'Invalid Writer {self.format}, default to JSON.')
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
                headers = {
                    'x-status': 'Empty Result',
                    'x-message': f"{err!s}"
                }
                return self.no_content(
                    headers=headers
                )
            except StatementError as err:
                headers = {
                    'x-status': 'Syntax Error',
                    'x-message': f"{err!s}"
                }
                return self.error(
                    "Query Syntax Error: {err}",
                    status=404,
                    exception=err,
                    headers=headers,
                    content_type='application/json'
                )
            except (DriverError, QueryException) as err:
                headers = {
                    'x-status': 'Query Error',
                    'x-message': f"{err!s}"
                }
                return self.error(
                    f"Query Error: {err}",
                    status=400,
                    exception=err,
                    headers=headers,
                    content_type='application/json'
                )
            except Exception as err:  # pylint: disable=W0703
                logging.exception(err)
                return self.error( # pylint: disable=E0702
                    message=f"Query Exception: {err}",
                    status=500,
                    exception=err,
                    content_type='application/json'
                )
            try:
                return await writer.get_response()
            except (TypeError, RuntimeError, ValueError) as err:
                headers={
                    'x-status': 'Output Error',
                    'x-message': f'Writer Error: {err}'
                }
                return self.error(
                    f"Output Error: {err}",
                    status=400,
                    exception=err,
                    headers=headers,
                    content_type='application/json'
                )
            except Exception as err:  # pylint: disable=W0703
                headers={
                    'x-status': 'QuerySource Error',
                    'x-message': f'Writer Error: {err}'
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
