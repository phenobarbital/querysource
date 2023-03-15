import time
from datetime import datetime, timedelta
from typing import Any, Union
from abc import ABC, abstractmethod
from hashlib import sha1
import traceback
from navconfig.logging import logging
from asyncdb.exceptions import NoDataFound, StatementError
from aiohttp import web
from aiohttp.web import Response, StreamResponse
from aiohttp.web_exceptions import (
    HTTPNoContent
)
from querysource.queries.abstract import BaseQuery
from querysource.libs.encoders import DefaultEncoder
from querysource.utils.functions import check_empty
from querysource.exceptions import (
    CacheException,
    DataNotFound,
    DriverError,
    QueryException,
)

mime_formats = {
    'plain': 'text/plain',
    'raw': 'text/plain',
    'json': 'application/json',
    'table': 'application/json',
    'dt': 'application/json',
    'txt': 'text/plain',
    'object': 'application/octet-stream',
    'csv': 'text/csv',
    'tsv': 'text/tsv',
    'jpg': 'image/jpeg',
    'png': 'image/png',
    'odt': 'application/vnd.oasis.opendocument.text',
    'ods': 'application/vnd.oasis.opendocument.spreadsheet',
    'pdf': 'application/pdf',
    'svg': 'image/svg+xml',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'excel': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'xlsm': 'application/vnd.ms-excel.sheet.macroEnabled.12',
    'xml': 'application/xml',
    'html': 'text/html',
    'avi': 'video/x-msvideo',
    'bokeh': 'text/html',
    'plotly': 'text/html',
    'pickle': 'application/octet-stream',
    'profiling': 'text/html',
    'report': 'text/html',
    'describe': 'application/json',
    'eda': 'text/html',
    'clustering': 'text/csv'
}

graph_ouputs = ('plotly', 'bokeh', 'matplotlib', 'seaborn',)

class AbstractWriter(ABC):
    """AbstractWriter.

    Description: Abstract class for defining Output Writers.
    """
    mimetype: str = 'text/plain'
    extension: str = '.txt'
    default_format: str = 'json'
    ctype: str = 'txt'
    output_format: str = 'native'

    def __init__(
        self,
        request: web.Request,
        resultset: Any,
        filename: str = None,
        response_type: str = 'web',
        download: bool = False,
        compression: Union[list, str] = None,
        ctype: str = None,
        **kwargs
    ):
        self.request = request
        self.query = resultset
        self.response_type = response_type
        self.download = download
        self._compression = compression
        self.logger = logging.getLogger('QS.Writer')
        self.data = None
        ## encoder:
        self._json = DefaultEncoder()
        ## content-type
        self.ctype = ctype
        try:
            self.content_type = mime_formats[self.ctype]
        except KeyError:
            self.content_type = mime_formats[self.default_format]
        self.filename = self.get_filename(filename)
        self.kwargs = kwargs

    def get_filename(self, filename: str, extension: str = None):
        dt = time.time()
        if extension:
            self.extension = extension
        return f"{dt}-{filename}{self.extension}"

    def enable_compression(self, response: web.StreamResponse) -> None:
        if self._compression is not None:
            if self._compression.strip().lower() == 'gzip':
                logging.debug('WORKING WITH GZIP')
                response.enable_compression(force=web.ContentCoding.gzip)
            elif self._compression.strip().lower() == 'deflate':
                logging.debug('WORKING WITH DEFLATE')
                response.enable_compression(force=web.ContentCoding.deflate)
            else:
                response.enable_compression()
        else:
            logging.debug('NO COMPRESSION')
            self._compression = None
            response.enable_compression(force=False)

    async def response(self, response_type: str = 'web', data: str = None) -> web.Response:
        """Returns a valid Web Response.
        TODO: making responses of Files, etc.
        """
        if response_type == 'web':
            response = Response(
                text=data,
                status=200,
                headers= {
                    'Pragma': "public",  # required,
                    'Expires': '0',
                    'Connection': 'keep-alive',
                    'Cache-Control': 'must-revalidate, post-check=0, pre-check=0',
                    'Content-Type': 'application/json',
                    "X-APPLICATION": "QuerySource"
                }
            )
            # response.content_length = len(data)
            logging.debug('returning a Basic Web Response')
        elif response_type == 'stream':
            current = datetime.utcnow()
            last_modified = current - timedelta(hours=1)
            response = StreamResponse(
                status=200,
                reason="OK",
                headers={
                    "Pragma": "no-cache",  # required,
                    "Last-Modified": last_modified.strftime(
                        "%a, %d %b %Y %H:%M:%S GMT"
                    ),
                    "Expires": "0",
                    "Connection": "keep-alive",
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "X-Content-Type-Options": "nosniff",
                    "Content-Type": self.content_type,
                    "Content-Transfer-Encoding": "binary",
                    "Date": current.strftime("%a, %d %b %Y %H:%M:%S GMT"),
                    "Content-Encoding": "identity",
                    "X-APPLICATION": "QuerySource"
                },
            )
            if self.download is True:
                self.enable_compression(response)
                response.headers["Content-Disposition"] = f"attachment; filename={self.filename}"
        else:
            response = StreamResponse(
                status=200,
                reason='OK',
                headers= {
                    'Pragma': "public",  # required,
                    'Expires': '0',
                    'Connection': 'keep-alive',
                    'Cache-Control': 'must-revalidate, post-check=0, pre-check=0',
                    'Content-Type': 'application/json',
                    "X-APPLICATION": "QuerySource"
                }
            )
            self.enable_compression(response)
        return response

    @abstractmethod
    async def get_response(self) -> Union[web.StreamResponse, Any]:
        """Return dataset converted to a Writer Output.

        get_response can return a Web Response, or else, the result of the "Writer".
        """

    async def stream_response(self, response: web.StreamResponse, data: Any) -> web.StreamResponse:
        content_length = len(data)
        response.content_length = content_length
        if self.response_type == 'stream': # an stream response:
            chunk_size = 16384
            response.headers[
                "Content-Range"
            ] = f"bytes 0-{chunk_size}/{content_length}"
            try:
                i = 0
                await response.prepare(self.request)
                while True:
                    chunk = data[i : i + chunk_size]
                    i += chunk_size
                    if not chunk:
                        break
                    await response.write(chunk)
                    # await response.drain()  # deprecated
                    # await asyncio.sleep(0.1)
                await response.write_eof()
                return response
            except Exception as ex: # pylint: disable=W0703
                return self.error(
                    message="Error Starting Stream Transmision",
                    exception=ex,
                    status=500
                )
        else: # basic stream response:
            await response.prepare(self.request)
            await response.write(data)
            await response.drain()  # switch point
            await response.write_eof()
            response.content_length = len(data)
            self.logger.debug('::: SENDING STREAM JSON RESPONSE: ')
            return response

    async def basic_response(self, response: web.StreamResponse, data: Any) -> web.StreamResponse:
        content_length = len(data)
        response.content_length = content_length
        digest = sha1(self.filename.encode('utf-8')).hexdigest()
        response.headers['X-Content-SHA1'] = f"{digest!s}"
        # await response.prepare(self.request)
        await response.write(data)
        await response.drain()  # switch point
        await response.write_eof()
        response.content_length = len(data)
        self.logger.debug('::: SENDING STREAM JSON RESPONSE: ')
        return response

    def error(
        self,
        message: str,
        status: int = 400,
        exception: BaseException = None,
        headers: dict = None,
        content_type: str = 'application/json'
    ):
        trace = None
        message = f"{message}: {exception!s}"
        if exception:
            trace = traceback.format_exc(limit=10)
        reason = {
            "error": str(message),
            "trace": trace
        }
        args = {
            "reason": self._json.dumps(reason),
            "headers": {
                "content_type": content_type,
                "X-MESSAGE": str(message),
                "X-STATUS": str(status)
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
            obj = web.HTTPInternalServerError(**args)
        else:
            obj = web.HTTPBadRequest(**args)
        if headers:
            for header, value in headers.items():
                obj.headers[header] = str(value)
        return obj

    def no_content(self, headers: dict = None, content_type: str = 'application/json') -> web.Response:
        response = HTTPNoContent(
            content_type=content_type
        )
        response.headers["Pragma"] = "no-cache"
        if headers:
            for header, value in headers.items():
                response.headers[header] = str(value)
        return response

    async def get_buffer(self):
        if isinstance(self.data, list):
            rec = self.data[0]
        else:
            rec = self.data
        try:
            self.columns = list(rec.keys())
        except (KeyError, AttributeError, TypeError) as e:
            self.columns = []
            self.logger.error(e)

    async def get_result(self):
        try:
            if isinstance(self.query, BaseQuery):
                self.data, error = await self.query.query(output_format = self.output_format)
            else:
                self.data = self.query
                error = None
            if error:
                if isinstance(error, DataNotFound):
                    raise DataNotFound(str(error))
                else:
                    raise QueryException(error)
            if check_empty(self.data):
                raise DataNotFound('Data not Found')
        except (StatementError, NoDataFound, DataNotFound):
            raise
        except CacheException as err:
            logging.error(
                f'QS: Error on Cache: {err}'
            )
        except (DriverError, QueryException, Exception):
            raise
