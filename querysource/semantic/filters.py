"""Validation and rendering of semantic filters (FEAT-175 M2)."""
from __future__ import annotations

from typing import Any

from .exceptions import SemanticCompilerUnavailable, SemanticRequestError
from .models import FILTER_OPS, CatalogField, SemanticFilter

ALLOWED_FUNCTIONS: frozenset[str] = frozenset(
    {"lower", "upper", "date_trunc", "coalesce", "abs", "round", "length"}
)


def _sqlglot() -> Any:
    """Lazy sqlglot import; missing → SemanticCompilerUnavailable (501)."""
    try:
        import sqlglot  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise SemanticCompilerUnavailable() from exc
    return sqlglot


class FilterValidator:
    """Validates caller and governance filters against the model catalog."""

    def __init__(self, dimensions: dict[str, CatalogField], *, dialect: str, max_filters: int) -> None:
        self._dimensions = dimensions
        self._dialect = dialect
        self._max_filters = max_filters

    def resolve_field(self, name: str) -> str:
        """Qualify ``name`` against the dimensions, rejecting unknown names."""
        if name in self._dimensions:
            return name
        matches = [field for field in self._dimensions if field.endswith(f".{name}")]
        if len(matches) == 1:
            return matches[0]
        available = ", ".join(sorted(self._dimensions))
        if not matches:
            raise SemanticRequestError(f"Unknown filter field {name!r}; available fields: {available}")
        raise SemanticRequestError(f"Ambiguous filter field {name!r}; use one of: {', '.join(sorted(matches))}")

    def render(self, filters: list[SemanticFilter | str]) -> list[str]:
        """Return Sidemantic filter strings; raises SemanticRequestError."""
        if len(filters) > self._max_filters:
            raise SemanticRequestError(f"Too many filters: {len(filters)} > {self._max_filters}")
        return [
            self._render_structured(flt) if isinstance(flt, SemanticFilter) else self._render_string(flt)
            for flt in filters
        ]

    def _render_structured(self, flt: SemanticFilter) -> str:
        sqlglot = _sqlglot()
        exp = sqlglot.exp
        field = self._column(self.resolve_field(flt.field), exp)
        op = flt.op.strip().lower()
        if op not in FILTER_OPS:
            raise SemanticRequestError(f"Unsupported semantic filter operator: {flt.op!r}")

        try:
            if op in {"is null", "is not null"}:
                if flt.value is not None:
                    raise SemanticRequestError(f"Operator {op!r} requires a null value")
                expression = exp.Is(this=field, expression=exp.Null())
                if op == "is not null":
                    expression = exp.Not(this=expression)
            elif op in {"in", "not in"}:
                if not isinstance(flt.value, (list, tuple)):
                    raise SemanticRequestError(f"Operator {op!r} requires a list value")
                expression = exp.In(this=field, expressions=[self._literal(value, exp) for value in flt.value])
                if op == "not in":
                    expression = exp.Not(this=expression)
            elif op == "between":
                if not isinstance(flt.value, (list, tuple)) or len(flt.value) != 2:
                    raise SemanticRequestError("Operator 'between' requires a two-item list value")
                expression = exp.Between(
                    this=field,
                    low=self._literal(flt.value[0], exp),
                    high=self._literal(flt.value[1], exp),
                )
            else:
                operators = {
                    "=": exp.EQ,
                    "!=": exp.NEQ,
                    ">": exp.GT,
                    ">=": exp.GTE,
                    "<": exp.LT,
                    "<=": exp.LTE,
                    "like": exp.Like,
                    "ilike": exp.ILike,
                }
                expression = operators[op](this=field, expression=self._literal(flt.value, exp))
        except SemanticRequestError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticRequestError(f"Invalid value for filter operator {op!r}") from exc
        return expression.sql(dialect=self._dialect)

    @staticmethod
    def _column(qualified: str, exp: Any) -> Any:
        """Build ``model.field`` as table + column, never a single dotted identifier."""
        table, _, name = qualified.rpartition(".")
        return exp.column(name, table=table or None)

    @staticmethod
    def _literal(value: Any, exp: Any) -> Any:
        """Convert a structured value to a sqlglot literal expression."""
        if isinstance(value, str):
            return exp.Literal.string(value)
        return exp.convert(value)

    def _render_string(self, text: str) -> str:
        sqlglot = _sqlglot()
        exp = sqlglot.exp
        try:
            expression = sqlglot.parse_one(text, dialect=self._dialect)
        except (sqlglot.errors.ParseError, SyntaxError, ValueError) as exc:
            raise SemanticRequestError(f"Invalid filter expression: {text!r}") from exc

        if any(isinstance(node, (exp.Subquery, exp.Select, exp.Command)) for node in expression.walk()):
            raise SemanticRequestError("Subqueries and SQL commands are not allowed in filters")
        for function in expression.find_all(exp.Func):
            names = {str(function.key).lower(), str(function.sql_name()).lower()}
            if getattr(function, "name", None):
                names.add(str(function.name).lower())
            if not names & ALLOWED_FUNCTIONS:
                raise SemanticRequestError(f"Function {function.name or function.key!r} is not allowed in filters")
        for column in list(expression.find_all(exp.Column)):
            column_name = column.name
            if column.table:
                column_name = f"{column.table}.{column_name}"
            column.replace(self._column(self.resolve_field(column_name), exp))
        return expression.sql(dialect=self._dialect)
