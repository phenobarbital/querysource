"""
querysource.auth.credentials — Per-user credential resolver for driver layer.

Implements a three-tier env-var lookup:
  1. Per-user:  ``<PREFIX>_<SANITIZED_USERNAME>_HOST/PORT/USER/PASSWORD/DATABASE``
  2. Profile:   ``<PREFIX>_<SANITIZED_PROFILE>_HOST/PORT/USER/PASSWORD/DATABASE``
  3. Default:   ``<PREFIX>_HOST/PORT/USER/PASSWORD/DATABASE``

Group-tier lookup is explicitly out of scope (FEAT-091 v1).
"""
import os
import logging
from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class ResolvedCredentials:
    """Connection parameters resolved by CredentialResolver.

    Args:
        host: Database hostname.
        port: Database port.
        user: Database user.
        password: Database password.
        database: Database name.
        source: Resolution tier used — one of:
            ``"user-override"``, ``"profile:<name>"``,
            or ``"default:<prefix>"``.
    """

    host: str
    port: int
    user: str
    password: str
    database: str
    source: str


class CredentialResolver:
    """Resolves driver connection params from session + env using a 3-tier lookup.

    Resolution order:
      1. Per-user env vars: ``<PREFIX>_<USERNAME>_HOST`` etc.
      2. Profile-from-policy env vars: ``<PREFIX>_<PROFILE>_HOST`` etc.
      3. Datasource default: ``<PREFIX>_HOST`` etc.

    Partial sets (only some of the 5 required vars set) fall through to the
    next tier. A warning is logged once per (tier, missing-key-set) pair.
    """

    _REQUIRED_FIELDS = ("HOST", "PORT", "USER", "PASSWORD", "DATABASE")

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        """Initialise the resolver.

        Args:
            logger: Optional logger; defaults to ``logging.getLogger(__name__)``.
        """
        self._logger = logger or logging.getLogger(__name__)
        # Dedup set: frozenset of (tier_label, missing_key) tuples already warned.
        self._warned: set = set()

    @staticmethod
    def sanitize(value: str) -> str:
        """Canonicalize a username or profile name for env-var key construction.

        Rules: uppercase; ``.``, ``-``, ``@`` → ``_``.

        Args:
            value: Raw username or profile string.

        Returns:
            Sanitized uppercase string safe for env-var lookup.
        """
        return value.upper().replace(".", "_").replace("-", "_").replace("@", "_")

    def _extract_username(self, session) -> Optional[str]:
        """Extract the username (or user_id) from a session dict.

        Args:
            session: Session object (dict-like).

        Returns:
            Username string, or None if not found.
        """
        if session is None:
            return None
        if hasattr(session, "get"):
            username = session.get("username") or session.get("user_id")
            if username:
                return str(username)
        return None

    def _lookup(self, prefix: str, segment: Optional[str]) -> Optional[dict]:
        """Look up the five required credential env vars for a given prefix+segment.

        Constructs keys like ``<PREFIX>_<SEGMENT>_HOST`` (with segment) or
        ``<PREFIX>_HOST`` (without segment). Returns ``None`` if any key is
        missing or if the PORT cannot be coerced to int.

        Args:
            prefix: E.g. ``"PG"`` or ``"DB"``.
            segment: Sanitized username/profile, or ``None`` for the default tier.

        Returns:
            Dict with keys ``host``, ``port``, ``user``, ``password``,
            ``database`` on full success; ``None`` on partial or missing set.
        """
        base = f"{prefix}_{segment}_" if segment else f"{prefix}_"
        values = {}
        missing = []
        for field in self._REQUIRED_FIELDS:
            key = f"{base}{field}"
            val = os.environ.get(key)
            if val is None:
                missing.append(key)
            else:
                values[field] = val

        if missing:
            if values:
                # Partial set — warn once per (segment, missing keys) combo.
                dedup_key = frozenset(missing)
                tier_label = f"{prefix}_{segment}" if segment else prefix
                warn_key = (tier_label, dedup_key)
                if warn_key not in self._warned:
                    self._warned.add(warn_key)
                    self._logger.warning(
                        "CredentialResolver: partial credential set for %s — "
                        "missing env vars %s; falling through to next tier.",
                        tier_label,
                        sorted(missing),
                    )
            return None

        try:
            port = int(values["PORT"])
        except (ValueError, TypeError):
            tier_label = f"{prefix}_{segment}" if segment else prefix
            dedup_key = frozenset([f"{base}PORT_invalid"])
            warn_key = (tier_label, dedup_key)
            if warn_key not in self._warned:
                self._warned.add(warn_key)
                self._logger.warning(
                    "CredentialResolver: invalid PORT value for %s; "
                    "falling through to next tier.",
                    tier_label,
                )
            return None

        return {
            "host": values["HOST"],
            "port": port,
            "user": values["USER"],
            "password": values["PASSWORD"],
            "database": values["DATABASE"],
        }

    def _build(self, source: str, creds: dict) -> ResolvedCredentials:
        """Construct a ResolvedCredentials from a raw dict + source label.

        Args:
            source: One of ``"user-override"``, ``"profile:<name>"``, or
                ``"default:<prefix>"``.
            creds: Dict with keys ``host``, ``port``, ``user``, ``password``,
                ``database``.

        Returns:
            ResolvedCredentials instance.
        """
        return ResolvedCredentials(
            host=creds["host"],
            port=creds["port"],
            user=creds["user"],
            password=creds["password"],
            database=creds["database"],
            source=source,
        )

    def resolve(
        self,
        prefix: str,
        session,
        credential_profile: Optional[str] = None,
    ) -> Optional[ResolvedCredentials]:
        """Resolve connection credentials using 3-tier env-var lookup.

        Resolution order:
          1. Per-user: ``<PREFIX>_<USERNAME>_*`` (if session has a username).
          2. Profile:  ``<PREFIX>_<PROFILE>_*`` (if credential_profile provided).
          3. Default:  ``<PREFIX>_*``.

        Partial sets fall through silently (with one warning per unique gap).
        If no tier resolves, returns ``None``.

        Args:
            prefix: Env-var prefix, e.g. ``"PG"`` or ``"DB"``.
            session: Dict-like session object; may be ``None``.
            credential_profile: Optional profile name from policy attributes.

        Returns:
            ResolvedCredentials or None.
        """
        # Tier 1: per-user
        if session is not None:
            username = self._extract_username(session)
            if username:
                creds = self._lookup(prefix, self.sanitize(username))
                if creds is not None:
                    return self._build("user-override", creds)

        # Tier 2: profile from policy
        if credential_profile:
            creds = self._lookup(prefix, self.sanitize(credential_profile))
            if creds is not None:
                return self._build(f"profile:{credential_profile}", creds)

        # Tier 3: datasource default
        creds = self._lookup(prefix, None)
        if creds is not None:
            return self._build(f"default:{prefix}", creds)

        return None
