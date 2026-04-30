"""Driver for pg (asyncPG) database connections.
"""
from dataclasses import InitVar
from datamodel import Column
from ...conf import (
    # postgres read-only
    asyncpg_url,
    PG_HOST,
    PG_PORT,
    PG_USER,
    PG_PWD,
    PG_DATABASE
)
from .abstract import SQLDriver


class pgDriver(SQLDriver):
    driver: str = 'pg'
    name: str = 'PostgreSQL (using asyncpg)'
    user: str
    username: InitVar
    hostname: InitVar
    dsn_format: str = Column(
        default="postgres://{user}:{password}@{host}:{port}/{database}",
        repr=False
    )
    port: int = Column(required=True, default=5432)
    # FEAT-091: env-var prefix for the credential resolver. Subclasses can
    # override (e.g. pg_adminDriver sets this to "DB").
    credential_prefix: str = "PG"

    def __post_init__(
        self,
        username: str = None,
        hostname: str = None,
        *args,
        **kwargs
    ):  # pylint: disable=W0613,W0221
        if hostname:
            self.host = hostname
        if username:
            self.user = username
        super().__post_init__(username, *args, **kwargs)

    def params(self) -> dict:
        """params

        Returns:
            dict: params required for AsyncDB.
        """
        return {
            "host": self.host,
            "port": self.port,
            "username": self.user,
            "password": self.password,
            "database": self.database
        }

    def params_for(self, session, app=None) -> dict:
        """Resolve connection params using the per-user credential resolver.

        Falls back to ``self.params()`` when:
          - PBAC is disabled (``app['credential_resolver']`` absent), or
          - The resolver returns ``None`` (no override matches).

        v1 scope: Postgres only. Other drivers continue using ``params()``.

        Args:
            session: The current user session (navigator_session SessionData
                or any mapping with ``get()``). May be ``None``.
            app: The aiohttp Application dict. May be ``None`` when called
                outside a request context (e.g. connection pooling at boot).

        Returns:
            dict: Connection params with keys matching ``params()`` shape.
        """
        if app is None:
            return self.params()
        resolver = app.get('credential_resolver')
        if resolver is None:
            return self.params()

        # Optional credential_profile: look in the session userinfo.
        # If the operator hasn't set this attribute, the resolver will
        # skip the profile tier and proceed to the default tier.
        credential_profile = None
        try:
            if session and hasattr(session, 'get'):
                userinfo = session.get('user', {}) or session.get('userinfo', {}) or {}
                if isinstance(userinfo, dict):
                    credential_profile = userinfo.get('credential_profile')
        except Exception:
            credential_profile = None

        try:
            resolved = resolver.resolve(
                prefix=self.credential_prefix,
                session=session,
                credential_profile=credential_profile,
            )
        except Exception:
            return self.params()

        if resolved is None:
            return self.params()
        return {
            "host": resolved.host,
            "port": resolved.port,
            "username": resolved.user,
            "password": resolved.password,
            "database": resolved.database,
        }

try:
    pg_default = pgDriver(
        dsn=asyncpg_url,
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PWD
    )
except ValueError:
    pg_default = None
