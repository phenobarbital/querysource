import traceback
from aiohttp import web
from aiohttp.web_exceptions import HTTPException
from navconfig import DEBUG
from navconfig.logging import logging
from navigator.views import BaseHandler
# Queries:
from querysource.queries.qs import QS
# Output
from querysource.outputs import mime_types
from querysource.outputs.writers import mime_formats
from querysource.exceptions import (
    QueryException
)

class AbstractHandler(BaseHandler):

    def post_init(self, *args, **kwargs):
        self.logger = logging.getLogger('QS.Handler')
        if not self.logger.handlers:
            logger_handler = logging.StreamHandler()  # Handler for the logger
        else:
            logger_handler = self.logger.handlers[0]
        logger_handler.setFormatter(
            logging.Formatter(
                '[%(levelname)s] %(asctime)s [%(name)s|%(lineno)d] :: %(message)s'
            )
        )
        self.logger.addHandler(logger_handler)
        self._lasterr = None
        self.slug: str = None
        self._compression: str = None
        self._columns: list = []
        self.debug: bool = DEBUG

    def format(self, request: web.Request, args: dict, ctype: str = None) -> str:
        """Extract Output format from Arguments.

        TODO: add @json declaration in QueryParams.
        """
        # determine using content negotiation
        if ctype is not None: ## Ctype passed by user:
            if ctype in mime_formats:
                return ctype
            else:
                f = 'json'
        elif accept:= request.headers.get('Content-Type'):
            f = mime_types[accept]
        elif accept := request.headers.get('Accept'):
            f = mime_types[accept]
        else:
            f = 'json'
        try:
            f = args['queryformat']
            del args['queryformat']
        except (KeyError, ValueError):
            pass
        finally:
            return f # pylint: disable=W0150

    def NoData(self, message: str = 'Data Not Found', headers: dict = None) -> web.Response:
        if not headers:
            headers = {
                "x-message": message
            }
        else:
            headers['x-message'] = message
        return web.Response(headers=headers, status=204)

    def NotFound(self, message: str, exception: BaseException = None):
        """Raised when Data not Found.
        """
        reason = {
            "message": message,
            "error": str(exception)
        }
        args = {
            "reason": self._json.dumps(reason),
            "content_type": "application/json",
        }
        raise web.HTTPNotFound(**args)

    def Error(
            self,
            reason: dict = None,
            message: str = None,
            exception: BaseException = None,
            code: int = 400
     ) -> HTTPException:
        """Error.

        Useful Function to raise Errors.
        Args:
            reason (dict): Message object
            message (str): Exception Message.
            exception (BaseException, optional): Exception captured. Defaults to None.
            code (int, optional): Error Code. Defaults to 500.
        """
        # message = f"{message}: {exception!s}"
        if not reason:
            reason = {
                "error": message,
                "reason": str(exception)
            }
        args = {
            "reason": message,
            "text": self._json.dumps(reason),
            "headers": {
                "X-MESSAGE": str(message),
                "X-STATUS": str(code)
            },
            "content_type": "application/json",
        }
        if code == 400:
            obj = web.HTTPBadRequest(**args)
        elif code == 401:
            obj = web.HTTPUnauthorized(**args)
        elif code == 403:  # forbidden
            obj = web.HTTPForbidden(**args)
        elif code == 404:  # not found
            obj = web.HTTPNotFound(**args)
        elif code == 406: # Not acceptable
            obj = web.HTTPNotAcceptable(**args)
        elif code == 412:
            obj = web.HTTPPreconditionFailed(**args)
        elif code == 428:
            obj = web.HTTPPreconditionRequired(**args)
        else:
            obj = web.HTTPBadRequest(**args)
        return obj

    def Except(
            self,
            reason: dict = None,
            message: str = None,
            exception: BaseException = None,
            headers: dict = None,
            code: int = 500
        ) -> HTTPException:
        trace = None
        if not headers:
            headers = {}
        if exception is not None:
            trace = traceback.format_exc(limit=20)
        if not reason:
            reason = {
                "error": message,
                "reason": str(exception),
                "trace": self._json.dumps(trace)
            }
        args = {
            "reason": message,
            "text": self._json.dumps(reason),
            "headers": {
                "X-MESSAGE": str(message),
                "X-STATUS": str(code),
                **headers
            },
            "content_type": "application/json",
        }
        if code == 500:
            obj = web.HTTPInternalServerError(**args)
        elif code == 501:
            obj = web.HTTPNotImplemented(**args)
        else:
            obj = web.HTTPServiceUnavailable(**args)
        return obj

    async def get_source(self, request, slug, conditions, **kwargs):
        try:
            query = QS(
                slug=slug,
                conditions=conditions,
                loop=self._loop,
                request=request,
                lazy=False,
                **kwargs
            )
            return query
        except Exception as err:
            self.logger.exception(err, stack_info=True)
            raise QueryException(
                f"Error getting QS provider for slug {slug}, error: {err}"
            ) from err
