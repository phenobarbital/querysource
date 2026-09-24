"""Public caller identity for programmatic (request-less) PBAC enforcement (FEAT-150)."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

_AUTHZ_GROUP = "authorized"


def _as_tuple(values: Iterable[Any] | None) -> tuple[str, ...]:
    """Normalize a str/sequence/None into a tuple of str (a bare str is one item)."""
    if values is None:
        return ()
    if isinstance(values, str):
        return (values,)
    return tuple(str(v) for v in values)


@dataclass(frozen=True)
class QSPrincipal:
    """Identity of the user a library caller acts on behalf of.

    Only identity/claims used by PBAC policies. ``tenant_id`` and ``channel``
    are informational (logs); they never select a store and never enter the
    evaluation userinfo. Raises ValueError when ``user_id`` is empty/blank, or
    when ``authz_backend`` is set and the other claims differ from the
    for_authz() shape. Sequences passed for groups/roles/programs are
    normalized to tuples of str.
    """

    user_id: str
    username: str | None = None
    groups: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    programs: tuple[str, ...] = ()
    superuser: bool = False
    tenant_id: str | None = None
    channel: str = "library"
    authz_backend: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "groups", _as_tuple(self.groups))
        object.__setattr__(self, "roles", _as_tuple(self.roles))
        object.__setattr__(self, "programs", _as_tuple(self.programs))

        if self.user_id is None or not str(self.user_id).strip():
            raise ValueError("QSPrincipal.user_id must not be empty or blank.")

        if self.authz_backend is not None:
            expected_user_id = f"authz:{self.authz_backend}"
            expected_groups = (_AUTHZ_GROUP, self.authz_backend)
            if (
                self.user_id != expected_user_id
                or self.username != expected_user_id
                or self.groups != expected_groups
                or self.roles != ()
                or self.programs != ()
                or self.superuser is not False
            ):
                raise ValueError(
                    "QSPrincipal with authz_backend must match the for_authz() shape "
                    "exactly: no extra groups, roles, programs or superuser."
                )

    @classmethod
    def for_authz(
        cls, backend: str, *, tenant_id: str | None = None, channel: str = "library"
    ) -> QSPrincipal:
        """Sessionless-authz identity identical to the handler's synthetic one.

        Mirrors ``handlers/abstract.py:382-386``: user_id = username =
        ``authz:<backend>``, groups ``("authorized", backend)``, no roles,
        programs or superuser. Raises ValueError on a blank backend.
        """
        backend = str(backend).strip() if backend is not None else ""
        if not backend:
            raise ValueError("QSPrincipal.for_authz() requires a non-blank backend.")
        identity = f"authz:{backend}"
        return cls(
            user_id=identity,
            username=identity,
            groups=(_AUTHZ_GROUP, backend),
            roles=(),
            programs=(),
            superuser=False,
            tenant_id=tenant_id,
            channel=channel,
            authz_backend=backend,
        )

    @property
    def is_authz(self) -> bool:
        """True when this is the sessionless-authz form."""
        return self.authz_backend is not None

    def to_userinfo(self) -> dict[str, Any]:
        """Return the navigator-auth userinfo dict the evaluator reads.

        User form: username (username or user_id), user_id, groups, roles,
        programs (lists), superuser (bool). Authz form: exactly
        {'username', 'groups', 'roles'} as the handler builds it.
        tenant_id / channel are never included.
        """
        if self.is_authz:
            return {
                "username": self.username,
                "groups": list(self.groups),
                "roles": list(self.roles),
            }
        return {
            "username": self.username or self.user_id,
            "user_id": self.user_id,
            "groups": list(self.groups),
            "roles": list(self.roles),
            "programs": list(self.programs),
            "superuser": self.superuser,
        }

    def log_fields(self) -> dict[str, Any]:
        """Return {'principal': user_id, 'principal_tenant': tenant_id, 'channel': channel} for logs."""
        return {
            "principal": self.user_id,
            "principal_tenant": self.tenant_id,
            "channel": self.channel,
        }
