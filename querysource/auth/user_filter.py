"""Per-user automatic filtering rules (``user_filtering`` / ``user_filter_options``).

Pure module: imports only ``querysource.auth.principal`` and
``querysource.exceptions`` so the Cython parser can import it lazily without
an import cycle and the multi-query follow-up can reuse it unchanged
(FEAT-184).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from ..exceptions import ConfigError, UserFilterDenied
from ..parsers.filter_values import has_between_prefix
from .principal import QSPrincipal

try:
    from navigator_auth.conf import AUTH_SESSION_OBJECT
except ImportError:
    AUTH_SESSION_OBJECT = "user"

_logger = logging.getLogger("QS.UserFilter")
_PRINCIPAL_CLAIMS: Final[frozenset[str]] = frozenset(
    {"user_id", "username", "groups", "roles", "programs", "superuser", "tenant_id"}
)
_AUTHZ_CLAIMS: Final[frozenset[str]] = frozenset({"groups", "roles"})

REASON_NO_PRINCIPAL: Final[str] = "no_principal"
REASON_AUTHZ_PRINCIPAL: Final[str] = "authz_principal"
REASON_MISSING_ATTRIBUTE: Final[str] = "missing_attribute"
REASON_PROGRAM_RESTRICTED: Final[str] = "program_restricted"
REASON_PARSER_REQUIRED: Final[str] = "parser_required"
REASON_INVALID_ATTRIBUTE: Final[str] = "invalid_attribute"

# Values with operator meaning in dialect filter_conditions() (parsers/sql.pyx).
_OPERATOR_TOKENS: Final[tuple[str, ...]] = ('<', '>', '>=', '<=', '<>', '!=', 'IS NOT', 'IS')
SUPERUSER_BYPASS: Final[str] = "superuser"
COLUMN_GRAMMAR: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


class UserFilterRule(BaseModel):
    """One column constrained by one identity attribute."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    column: str
    attribute: str
    operator: Literal["eq", "in"] = "eq"
    required: bool = True

    @field_validator("column")
    @classmethod
    def _column_grammar(cls, value: str) -> str:
        """Reject anything but ``column`` or ``alias.column`` (it is spliced into SQL)."""
        if COLUMN_GRAMMAR.fullmatch(value) is None:
            raise ValueError("column must be a column name or alias.column")
        return value

    @field_validator("attribute")
    @classmethod
    def _attribute_not_blank(cls, value: str) -> str:
        """Reject a blank attribute path."""
        value = value.strip()
        if not value:
            raise ValueError("attribute must not be blank")
        return value


class ProgramRestriction(BaseModel):
    """Deny unless the definition's program_slug is among the caller's programs."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    restricted: bool = False
    allow_default: bool = True
    attribute: str = "programs"


class UserFilterOptions(BaseModel):
    """Validated ``user_filter_options`` document."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    rules: tuple[UserFilterRule, ...] = ()
    program: ProgramRestriction = ProgramRestriction()
    bypass: tuple[str, ...] = (SUPERUSER_BYPASS,)

    @model_validator(mode="after")
    def _declares_something(self) -> UserFilterOptions:
        """A switch without rules nor program restriction is a configuration mistake."""
        if not self.rules and not self.program.restricted:
            raise ValueError("user_filtering is on but user_filter_options declares nothing")
        return self


@dataclass(frozen=True)
class UserFilterResult:
    """Outcome of compiling the rules for one caller (allow or bypass)."""

    predicates: dict[str, Any] = field(default_factory=dict)
    applied: tuple[str, ...] = ()
    bypassed: bool = False
    bypass_reason: str | None = None


def parse_user_filter_options(raw: Mapping[str, Any] | UserFilterOptions | None) -> UserFilterOptions:
    """Validate the jsonb document into ``UserFilterOptions``.

    Raises:
        ConfigError: ``raw`` is None/empty, not a mapping, fails validation, or
            declares neither rules nor a program restriction.
    """
    if isinstance(raw, UserFilterOptions):
        return raw
    if not isinstance(raw, Mapping) or not raw:
        raise ConfigError("user_filter_options is required when user_filtering is on")
    try:
        return UserFilterOptions.model_validate(dict(raw))
    except (ValidationError, ValueError, TypeError) as err:
        raise ConfigError(f"Invalid user_filter_options: {err}") from err


def session_userinfo(session: Any | None) -> dict[str, Any]:
    """Return the raw navigator-auth userinfo dict of a session, or ``{}``."""
    if session is None or not hasattr(session, "get"):
        return {}
    userinfo = session.get(AUTH_SESSION_OBJECT, session.get("user", {}))
    return dict(userinfo) if isinstance(userinfo, dict) else {}


def resolve_attribute(path: str, principal: QSPrincipal | None, userinfo: Mapping[str, Any] | None) -> Any:
    """Resolve a dotted path: typed principal claim first, then ``userinfo``; ``None`` when absent."""
    head, *rest = path.split(".")
    value: Any = None
    if principal is not None and head in _PRINCIPAL_CLAIMS and (not principal.is_authz or head in _AUTHZ_CLAIMS):
        value = getattr(principal, head)
    if (value is None or value == ()) and userinfo:
        value = userinfo.get(head)
    for segment in rest:
        if not isinstance(value, Mapping) or segment not in value:
            return None
        value = value[segment]
    return list(value) if isinstance(value, tuple) else value


def bypass_reason(options: UserFilterOptions, principal: QSPrincipal | None) -> str | None:
    """Return the matching superuser, group, or role bypass reason."""
    if principal is None:
        return None
    normalized_bypass = {name.strip().lower() for name in options.bypass}
    if SUPERUSER_BYPASS in normalized_bypass and principal.superuser:
        return SUPERUSER_BYPASS
    for group in principal.groups:
        if group.strip().lower() in normalized_bypass:
            return f"group:{group}"
    for role in principal.roles:
        if role.strip().lower() in normalized_bypass:
            return f"role:{role}"
    return None


def _normalize_programs(raw: Any) -> set[str]:
    """Normalize strings, dictionaries, and objects with slug/name into lower-case slugs."""
    if raw is None:
        return set()
    if isinstance(raw, (str, dict)):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = [raw]

    programs: set[str] = set()
    for item in items:
        if item is None:
            continue
        if hasattr(item, "slug"):
            value = item.slug
        elif hasattr(item, "name"):
            value = item.name
        elif isinstance(item, dict):
            value = item.get("slug") or item.get("name")
        else:
            value = str(item)
        if value is not None and (slug := str(value).strip().lower()):
            programs.add(slug)
    return programs


def check_program(
    options: UserFilterOptions,
    program_slug: str | None,
    principal: QSPrincipal | None,
    userinfo: Mapping[str, Any] | None,
    *,
    slug: str | None = None,
) -> None:
    """Raise ``UserFilterDenied(program_restricted)`` unless the program check passes."""
    program = options.program
    if not program.restricted:
        return
    slug_lc = (program_slug or "").strip().lower()
    if program.allow_default and slug_lc == "default":
        return
    programs = _normalize_programs(resolve_attribute(program.attribute, principal, userinfo))
    if not slug_lc or slug_lc not in programs:
        raise UserFilterDenied(reason_code=REASON_PROGRAM_RESTRICTED, slug=slug)


def _safe_scalar(value: Any) -> bool:
    """Return True if *value* is a plain scalar with no filter-operator meaning."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("!") or value.lower() == "null":
        return False
    if has_between_prefix(value):
        return False
    return value.strip().upper() not in _OPERATOR_TOKENS


def _safe_filter_value(value: Any) -> bool:
    """Return True if *value* can be passed to filter_conditions as a literal equality/IN value."""
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        return bool(items) and all(_safe_scalar(v) for v in items)
    return _safe_scalar(value)


def compile_user_filter(
    options: Mapping[str, Any] | UserFilterOptions | None,
    *,
    program_slug: str | None,
    principal: QSPrincipal | None,
    userinfo: Mapping[str, Any] | None,
    slug: str | None = None,
    logger: logging.Logger | None = None,
) -> UserFilterResult:
    """Compile the definition's rules into parser predicates for one caller."""
    log = logger or _logger
    opts = parse_user_filter_options(options)
    log_fields = principal.log_fields() if principal else {}
    if principal is None and not userinfo:
        log.warning("user_filter denied slug=%s reason=%s %s", slug, REASON_NO_PRINCIPAL, log_fields)
        raise UserFilterDenied(reason_code=REASON_NO_PRINCIPAL, slug=slug)

    reason = bypass_reason(opts, principal)
    if reason:
        log.info("user_filter bypassed slug=%s reason=%s %s", slug, reason, log_fields)
        return UserFilterResult(bypassed=True, bypass_reason=reason)

    try:
        check_program(opts, program_slug, principal, userinfo, slug=slug)
    except UserFilterDenied as exc:
        log.warning("user_filter denied slug=%s reason=%s %s", slug, exc.reason_code, log_fields)
        raise

    predicates: dict[str, Any] = {}
    applied: list[str] = []
    for rule in opts.rules:
        value = resolve_attribute(rule.attribute, principal, userinfo)
        missing = value is None or (isinstance(value, (list, tuple)) and not value)
        if missing:
            if not rule.required:
                continue
            reason_code = (
                REASON_AUTHZ_PRINCIPAL
                if principal is not None
                and principal.is_authz
                and rule.attribute.split(".", 1)[0] in _PRINCIPAL_CLAIMS - _AUTHZ_CLAIMS
                else REASON_MISSING_ATTRIBUTE
            )
            log.warning("user_filter denied slug=%s reason=%s %s", slug, reason_code, log_fields)
            raise UserFilterDenied(reason_code=reason_code, slug=slug)
        if not _safe_filter_value(value):
            log.warning(
                "user_filter deny: invalid attribute value type for %s (column %s, slug %s)",
                rule.attribute, rule.column, slug,
            )
            raise UserFilterDenied(reason_code=REASON_INVALID_ATTRIBUTE, slug=slug)
        if isinstance(value, (list, tuple)) or rule.operator == "in":
            predicates[rule.column] = list(value) if isinstance(value, (list, tuple)) else [value]
        else:
            predicates[rule.column] = value
        applied.append(rule.column)
    return UserFilterResult(predicates=predicates, applied=tuple(applied))
