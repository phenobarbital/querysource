import traceback
from typing import Optional
from aiohttp import web
from aiohttp.web_exceptions import HTTPException
from navconfig import DEBUG
from navconfig.logging import logging
from navigator.views import BaseHandler
from navigator_session import get_session, SessionData
# Queries:
from ..queries.qs import QS
# Output Formats:
from ..types import mime_formats, mime_types
from ..exceptions import (
    QueryException
)
from ..utils.events import enable_uvloop

enable_uvloop()

# Sentinel used to distinguish "not yet cached" from "cached as None".
_SENTINEL = object()


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

    def format(
        self,
        request: web.Request,
        args: dict,
        ctype: str = None
    ) -> str:
        """Extract Output format from Arguments.

        TODO: add @json declaration in QueryParams.
        """
        # determine using content negotiation
        f = None
        try:
            if accept := request.headers.get('Content-Type'):
                f = mime_types[accept]
            elif accept := request.headers.get('Accept'):
                f = mime_types[accept]
        except KeyError:
            pass
        if ctype is not None:  # Ctype passed by user:
            if ctype in mime_formats:
                return ctype
            else:
                f = 'json'
        try:
            f = args['queryformat']
            del args['queryformat']
        except (KeyError, ValueError):
            pass
        finally:
            return f  # pylint: disable=W0150

    def NoData(
        self,
        message: str = 'Data Not Found',
        headers: dict = None
    ) -> web.Response:
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
        stacktrace: str = None,
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
        try:
            reason_exception = f"{exception.decode()!s}"
        except Exception:
            reason_exception = str(exception)
        if not reason:
            reason = {
                "error": message,
                "reason": reason_exception
            }
        if stacktrace:
            reason["trace"] = stacktrace
        args = {
            "reason": message,
            "text": self._json.dumps(reason),
            "headers": {
                "X-MESSAGE": str(message),
                "X-STATUS": str(code),
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
        elif code == 406:  # Not acceptable
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
        stacktrace: str = None,
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
        if stacktrace:
            reason["trace"] = stacktrace
        args = {
            "reason": message,
            "text": self._json.dumps(reason),
            "headers": {
                "X-MESSAGE": str(message),
                "X-STATUS": str(code),
                "X-ERROR": str(exception),
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

    async def get_source(
        self,
        request,
        slug,
        conditions,
        **kwargs
    ) -> QS:
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

    # ── FEAT-091: PBAC helpers ────────────────────────────────────────────

    async def _get_user_session(
        self,
        request: web.Request,
    ) -> Optional[SessionData]:
        """Extract and memoize the user session from the current request.

        Uses navigator_session.get_session(). Memoizes the result on
        ``request['user_session']`` so subsequent calls within the same
        request are free. Returns ``None`` when navigator_session is
        unavailable or no session exists.

        Args:
            request: The current aiohttp web request.

        Returns:
            SessionData or None.
        """
        cached = request.get('user_session', _SENTINEL)
        if cached is not _SENTINEL:
            return cached
        try:
            session = await get_session(request, new=False)
        except RuntimeError:
            self.logger.error('QS: User Session system is not installed.')
            session = None
        request['user_session'] = session
        return session

    async def _enforce_pbac(
        self,
        request: web.Request,
        resource_type,
        resource_name: str,
        action: str,
    ) -> None:
        """Evaluate a single PBAC decision; raise web.HTTPNotFound on deny.

        Fast-path no-op when PBAC is not active (``app['security']`` absent).
        Fail-closed: if PBAC is enabled but no session can be extracted, the
        request is denied with 404.

        Args:
            request: The current aiohttp web request.
            resource_type: navigator_auth ResourceType (or string shim value).
            resource_name: The resource identifier string.
            action: The action string, e.g. ``"slug:execute"``.

        Raises:
            web.HTTPNotFound: When the evaluator denies access, or when
                PBAC is enabled but the request has no user session.
        """
        guardian = request.app.get('security')
        if guardian is None:
            return  # PBAC disabled — fast-path no-op

        session = await self._get_user_session(request)
        if session is None:
            # Fail-closed: no session → deny
            self.logger.info(
                "PBAC denied (no session): %s/%s action=%s",
                resource_type,
                resource_name,
                action,
            )
            raise web.HTTPNotFound()

        evaluator = request.app.get('policy_evaluator')
        if evaluator is None:
            # Bootstrap inconsistency — Guardian set but no evaluator.
            self.logger.error(
                "PBAC misconfigured: 'security' is set but 'policy_evaluator' is missing"
            )
            raise web.HTTPNotFound()

        # Lazy-import navigator-auth EvalContext (only when PBAC is active).
        from navigator_auth.abac.context import EvalContext
        from navigator_auth.abac.policies.environment import Environment
        from navigator_auth.conf import AUTH_SESSION_OBJECT

        userinfo = (
            session.get(AUTH_SESSION_OBJECT, {})
            if hasattr(session, 'get') else {}
        )
        if not isinstance(userinfo, dict):
            userinfo = {}
        user = userinfo if userinfo else None

        ctx = EvalContext(
            request=request,
            user=user,
            userinfo=userinfo,
            session=session,
        )

        result = evaluator.check_access(
            ctx=ctx,
            resource_type=resource_type,
            resource_name=resource_name,
            action=action,
            env=Environment(),
        )
        if not result.allowed:
            self.logger.info(
                "PBAC denied: %s/%s action=%s policy=%s reason=%s",
                resource_type,
                resource_name,
                action,
                getattr(result, 'matched_policy', None),
                getattr(result, 'reason', None),
            )
            raise web.HTTPNotFound()
