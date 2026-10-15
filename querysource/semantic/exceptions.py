"""Semantic-layer exceptions (FEAT-175). Every semantic failure derives from SemanticError."""
from __future__ import annotations

from ..exceptions import QueryException


class SemanticError(QueryException):
    """Base of every semantic-layer error; re-raised unchanged by QS.build_provider."""

    default_code: int = 500

    def __init__(self, message: str = "", code: int | None = None, **kwargs) -> None:
        """Initialize an error with its subclass's default status code."""
        super().__init__(message, code=code, **kwargs)


class SemanticRequestError(SemanticError):
    """Invalid caller request: unknown field, bad operator/grain/limit, unparsable filter."""

    default_code: int = 400


class SemanticResourceError(SemanticError):
    """Malformed resource YAML / model document, path escape, or non-SQL datasource."""

    default_code: int = 400


class SemanticGovernanceError(SemanticError):
    """Invalid governance.row_filters entry (fail-closed configuration error)."""

    default_code: int = 500


class SemanticCompilerUnavailable(SemanticError):
    """The optional compiler is not installed."""

    default_code: int = 501

    def __init__(self, message: str | None = None, **kwargs) -> None:
        """Initialize the compiler-installation guidance error."""
        super().__init__(
            message or "Semantic compiler unavailable: install querysource[semantic]",
            code=501,
            **kwargs,
        )


class SemanticFieldDenied(SemanticError):
    """PBAC or Sidemantic security denied a metric/dimension; names the field."""

    default_code: int = 403

    def __init__(self, field: str, message: str | None = None, **kwargs) -> None:
        """Initialize the denial and retain the denied semantic field."""
        self.field = field
        super().__init__(message or f"Access denied to semantic field '{field}'", code=403, **kwargs)
