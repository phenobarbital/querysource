import inspect
from typing import Optional
from aiohttp import web
from aiohttp.web_exceptions import HTTPException
from navconfig import DEBUG
from navconfig.logging import logging
from navigator.views import BaseHandler
from navigator_session import get_session, SessionData
# Config:
from ..conf import QS_PBAC_ALLOW_SESSIONLESS_AUTHZ
# Queries:
from ..queries.qs import QS
# Output Formats:
from ..types import mime_formats, mime_types
from ..exceptions import (
    QueryException
)
from ..utils.errors import build_error_payload
from ..utils.events import enable_uvloop

enable_uvloop()

# Sentinel used to distinguish "not yet cached" from "cached as None".
_SENTINEL = object()


class AbstractHandler(BaseHandler):
    # Class-level default so error helpers (Error/critical/Except) can always
    # read ``self.debug`` even if ``post_init`` has not run (e.g. instances
    # built via ``__new__`` in tests). ``post_init`` overrides it per-instance.
    debug: bool = DEBUG

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
        # aiohttp's header serializer rejects non-str values (and reports the
        # error as "non-str key None"). Drop None entries and stringify the
        # rest so callers can pass raw values without manual sanitisation.
        clean_headers = {
            str(k): str(v) for k, v in headers.items() if v is not None
        }
        return web.Response(headers=clean_headers, status=204)

    def NotFound(self, message: str, exception: BaseException = None):
        """Raised when Data not Found.

        Routes through ``build_error_payload`` so that in production
        (``self.debug=False``) the response body never contains raw exception
        text or internal paths.
        """
        payload = build_error_payload(
            category="not_found",
            status=404,
            exception=exception,
            debug=self.debug,
            logger=self.logger,
            public_message=message if self.debug else None,
        )
        args = {
            "reason": payload["error"],
            "text": self._json.dumps(payload),
            "headers": {
                "X-MESSAGE": payload["error"],
                "X-STATUS": "404",
            },
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

        Builds a client-safe error payload via ``build_error_payload``.
        In production (``self.debug=False``) no traceback, raw DB text, or
        internal paths are exposed to the client; full detail is logged
        server-side tagged with ``error_id``.

        Args:
            reason (dict): Ignored — present for backwards-compatibility.
                The body is now always built by ``build_error_payload``.
            message (str): Exception message / short description.
            exception (BaseException, optional): Caught exception. Defaults to None.
            stacktrace (str, optional): Pre-captured traceback string.
                Forwarded to the server log only; never in the client body.
            code (int, optional): HTTP error code. Defaults to 400.
        """
        # Map HTTP status code to a formatter category
        if code == 404:
            category = "not_found"
        elif code in (400, 401, 402, 403, 406, 412, 422, 428):
            category = "query_error"
        else:
            category = "server_error"

        payload = build_error_payload(
            category=category,
            status=code,
            exception=exception,
            debug=self.debug,
            logger=self.logger,
            public_message=message if self.debug else None,
        )
        args = {
            "reason": payload["error"],
            "text": self._json.dumps(payload),
            "headers": {
                "X-MESSAGE": payload["error"],
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
        elif code == 422:  # Unprocessable Entity (e.g. data/validation errors)
            obj = web.HTTPUnprocessableEntity(**args)
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
        """Except.

        Handles unexpected / server-side exceptions.

        Delegates to ``build_error_payload`` which logs the full traceback
        server-side tagged with ``error_id``.  In production (``self.debug=False``)
        the client body contains only ``error``, ``status``, and ``error_id`` —
        no traceback, no raw exception text, no ``X-ERROR`` header.

        Args:
            reason (dict): Ignored — present for backwards-compatibility.
            message (str): Short description of the failure context.
            exception (BaseException, optional): Caught exception.
            stacktrace (str, optional): Pre-captured traceback string (logged only).
            headers (dict, optional): Extra headers to add to the response.
            code (int, optional): HTTP status code. Defaults to 500.
        """
        if not headers:
            headers = {}

        payload = build_error_payload(
            category="server_error",
            status=code,
            exception=exception,
            debug=self.debug,
            logger=self.logger,
            public_message=message if self.debug else None,
        )
        response_headers: dict = {
            "X-MESSAGE": payload["error"],
            "X-STATUS": str(code),
            **headers,
        }
        # Only expose the raw exception string in the X-ERROR header when debug
        if self.debug and exception is not None:
            # HTTP header values may not contain embedded CR/LF (aiohttp
            # raises on write) — FEAT-146 routes multi-line OutputError text
            # through this path via Except(code=500), so collapse to a
            # single line here too (same treatment as payload["error"] in
            # build_error_payload and the X-Output-Errors header).
            response_headers["X-ERROR"] = " ".join(str(exception).splitlines())

        args = {
            "reason": payload["error"],
            "text": self._json.dumps(payload),
            "headers": response_headers,
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

        # Fail-closed: callers must always supply a real resource_name.
        # A ``None`` (or empty) name means the route bound a missing path
        # parameter (e.g. ``slug`` was not in args) — short-circuit to 404
        # instead of feeding ``None`` into navigator-auth, where it could
        # match the wrong policy or raise an internal error.
        if not resource_name:
            self.logger.info(
                "PBAC denied (missing resource_name): %s action=%s",
                resource_type,
                action,
            )
            raise web.HTTPNotFound()

        session = await self._get_user_session(request)
        authz_userinfo = None
        if session is None:
            # Sessionless authorization: requests authorized by a
            # navigator-auth authz backend (IP / host / User-Agent, e.g.
            # authz_allowed_ips, authz_useragent) legitimately carry no user
            # session. When QS_PBAC_ALLOW_SESSIONLESS_AUTHZ is enabled and
            # navigator-auth stamped the request, do NOT bypass PBAC: evaluate
            # it under a synthetic identity whose groups are ``authorized``
            # plus the granting backend name, so explicit allow policies
            # (e.g. policies/authorized.yaml) decide what such clients may do.
            if QS_PBAC_ALLOW_SESSIONLESS_AUTHZ:
                try:
                    from navigator_auth.conf import AUTHZ_BACKEND_KEY
                except ImportError:
                    AUTHZ_BACKEND_KEY = 'authz_backend'
                authz_backend = request.get(AUTHZ_BACKEND_KEY)
                if authz_backend:
                    backend = str(authz_backend)
                    authz_userinfo = {
                        'username': f'authz:{backend}',
                        'groups': ['authorized', backend],
                        'roles': [],
                    }
                    self.logger.info(
                        "PBAC sessionless authz (backend=%s): evaluating "
                        "%s/%s action=%s as group 'authorized'",
                        backend,
                        resource_type,
                        resource_name,
                        action,
                    )
            if authz_userinfo is None:
                # Fail-closed: no session and no sessionless authz → deny
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

        if authz_userinfo is not None:
            # Authorized-but-not-authenticated: synthetic identity, no user.
            userinfo = authz_userinfo
            user = None
        else:
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

        # ``PolicyEvaluator.check_access`` is currently synchronous in
        # navigator-auth (Rust-backed). The defensive ``iscoroutine`` await
        # below guarantees forward-compatibility if upstream ever flips it
        # to ``async def`` — without that guard, a coroutine return value
        # would be truthy and silently bypass enforcement.
        result = evaluator.check_access(
            ctx=ctx,
            resource_type=resource_type,
            resource_name=resource_name,
            action=action,
            env=Environment(),
        )
        if inspect.iscoroutine(result):
            result = await result
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
