"""Per-user automatic filtering rules (``user_filtering`` / ``user_filter_options``).

Pure module: imports only ``querysource.auth.principal`` and
``querysource.exceptions`` so the Cython parser can import it lazily without
an import cycle and the multi-query follow-up can reuse it unchanged
(FEAT-184).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from ..exceptions import ConfigError

REASON_NO_PRINCIPAL: Final[str] = "no_principal"
REASON_AUTHZ_PRINCIPAL: Final[str] = "authz_principal"
REASON_MISSING_ATTRIBUTE: Final[str] = "missing_attribute"
REASON_PROGRAM_RESTRICTED: Final[str] = "program_restricted"
REASON_PARSER_REQUIRED: Final[str] = "parser_required"
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
