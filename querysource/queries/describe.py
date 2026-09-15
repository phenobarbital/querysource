"""Pure describer for stored query slugs (FEAT-148).

Transforms a stored definition into the ``GET /api/v1/queries/{slug}/describe`` payload.
No I/O: no request, database, provider or parser access.
"""
from __future__ import annotations

import json
import re
import string
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:  # pragma: no cover
    from ..models import QueryModel

RESERVED_PLACEHOLDERS: frozenset[str] = frozenset({
    "schema", "table", "tablename", "fields", "filter", "where_cond", "and_cond",
    "grouping", "group_by", "ordering", "order_by", "offset", "_offset",
    "limit", "_limit", "querylimit",
})
CANONICAL_TYPES: frozenset[str] = frozenset({
    "literal", "integer", "float", "numeric", "decimal", "epoch", "boolean", "string",
    "field", "date", "datetime", "timestamp", "uuid", "array", "json",
    "numrange", "int4range", "int8range",
})
CANONICAL_ALIASES: dict[str, str] = {"int": "integer", "varchar": "string"}
KEYWORD_TYPES: frozenset[str] = frozenset({"date", "datetime", "timestamp"})
# Mirrors querysource/providers/sql.py:37-45 (sqlProvider.replacement).
IMPLICIT_DEFAULTS: dict[str, str] = {
    "firstdate": "current_date", "lastdate": "current_date", "filterdate": "current_date",
}
RAW_FIELDS: tuple[str, ...] = ("query_raw",)
ADMIN_FIELDS: tuple[str, ...] = ("dwh_info", "dwh_scheduler", "cache_options", "created_by", "updated_by")
_KEY_GRAMMAR = re.compile(r"^[A-Za-z0-9_.]+$")  # rust/src/safe_dict.rs:116-120

VariableSource = Literal["cond_definition", "conditions", "placeholder"]


class DescribeVariable(BaseModel):
    """One user-facing input of a slug."""

    model_config = ConfigDict(frozen=True)
    name: str
    type: str | None = None
    raw_type: str | None = None
    default: Any = None
    required: bool
    source: VariableSource
    accepts_keywords: bool


class DescribeGrants(BaseModel):
    """What the caller may see beyond the base payload."""

    model_config = ConfigDict(frozen=True)
    raw: bool = False
    admin: bool = False


def extract_placeholders(query_raw: str | None) -> tuple[list[str] | None, str | None]:
    """Return (ordered unique placeholder names, error); ([], None) for empty input."""
    if not query_raw:
        return [], None
    names: list[str] = []
    seen: set[str] = set()
    try:
        for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(query_raw):
            if field_name is None:
                continue
            if not _KEY_GRAMMAR.match(field_name):
                continue
            if field_name in seen:
                continue
            seen.add(field_name)
            names.append(field_name)
    except ValueError as err:
        return None, str(err)
    return names, None


def is_json_dialect(query_raw: str | None) -> bool:
    """True when query_raw is a JSON object/array (Mongo/Elastic/Arango style)."""
    if not query_raw:
        return False
    stripped = query_raw.strip()
    if not stripped or stripped[0] not in ("{", "["):
        return False
    try:
        json.loads(stripped)
    except (ValueError, TypeError):
        return False
    return True


def normalize_type(raw_type: str | None) -> str | None:
    """Canonical lowercase type or None (aliases int→integer, varchar→string)."""
    if raw_type is None:
        return None
    lowered = str(raw_type).strip().lower()
    lowered = CANONICAL_ALIASES.get(lowered, lowered)
    if lowered in CANONICAL_TYPES:
        return lowered
    return None


def build_variables(
    query_raw: str | None, conditions: dict | None, cond_definition: dict | None
) -> dict:
    """Return {'variables', 'variables_supported', 'structural_placeholders', 'warnings'[, 'variables_error']}."""
    conditions = dict(conditions or {})
    conditions.pop("cond_definition", None)
    cond_definition = dict(cond_definition or {})
    warnings: list[str] = []
    if is_json_dialect(query_raw):
        return {"variables": None, "variables_supported": False,
                "structural_placeholders": [], "warnings": warnings}
    names, error = extract_placeholders(query_raw)
    if error is not None:
        return {"variables": None, "variables_supported": True, "structural_placeholders": [],
                "warnings": warnings, "variables_error": error}

    structural: list[str] = []
    variable_names: list[str] = []
    for name in names:
        if name in RESERVED_PLACEHOLDERS:
            structural.append(name)
            if name in cond_definition:
                warnings.append(
                    f"reserved placeholder '{name}' declared in cond_definition"
                )
        else:
            variable_names.append(name)

    # Append cond_definition-only keys (not already seen as placeholders), in key order.
    seen = set(names)
    for name in cond_definition:
        if name not in seen:
            variable_names.append(name)
            seen.add(name)

    variables: list[DescribeVariable] = []
    for name in variable_names:
        raw_type = cond_definition.get(name)
        var_type = normalize_type(raw_type)
        if raw_type is not None and var_type is None:
            warnings.append(f"unknown type '{raw_type}' for '{name}'")

        if name in conditions:
            default = conditions[name]
        else:
            default = IMPLICIT_DEFAULTS.get(name)
        required = name not in conditions and name not in IMPLICIT_DEFAULTS

        if name in cond_definition:
            source: VariableSource = "cond_definition"
        elif name in conditions:
            source = "conditions"
        else:
            source = "placeholder"

        accepts_keywords = var_type in KEYWORD_TYPES or raw_type is None

        variables.append(
            DescribeVariable(
                name=name,
                type=var_type,
                raw_type=raw_type,
                default=default,
                required=required,
                source=source,
                accepts_keywords=accepts_keywords,
            )
        )

    return {
        "variables": variables,
        "variables_supported": True,
        "structural_placeholders": structural,
        "warnings": warnings,
    }


def redact_payload(payload: dict, grants: DescribeGrants) -> tuple[dict, list[str]]:
    """Return (new payload without restricted fields, sorted redacted names)."""
    restricted: set[str] = set()
    if not grants.raw:
        restricted.update(RAW_FIELDS)
    if not grants.admin:
        restricted.update(ADMIN_FIELDS)

    redacted_names: list[str] = []
    new_payload: dict = {}
    for key, value in payload.items():
        if key in restricted:
            redacted_names.append(key)
            continue
        new_payload[key] = value

    return new_payload, sorted(redacted_names)


def describe_slug(
    definition: QueryModel | dict,
    grants: DescribeGrants,
    *,
    columns_link: str,
    vocabulary_link: str,
) -> dict:
    """Full detail payload: model fields (redacted) + 'derived' + 'redacted'."""
    from ..models import QueryModel  # lazy: keeps module import cheap for pure use

    def _get(name: str) -> Any:
        if isinstance(definition, dict):
            return definition.get(name)
        return getattr(definition, name, None)

    base = {name: _get(name) for name in QueryModel.columns(QueryModel)}

    query_raw = base.get("query_raw")
    conditions = base.get("conditions") or {}
    cond_definition = base.get("cond_definition") or {}

    variables_result = build_variables(query_raw, conditions, cond_definition)
    variables = variables_result["variables"]
    variables_dump = (
        [v.model_dump() for v in variables] if variables is not None else None
    )

    effective_cond_definition = {
        **(dict(conditions).get("cond_definition") or {}),
        **cond_definition,
    }

    capabilities = {
        "fields": base.get("fields") or [],
        "filtering": base.get("filtering") or {},
        "ordering": base.get("ordering") or [],
        "grouping": base.get("grouping") or [],
        "h_filtering": bool(base.get("h_filtering") or False),
        "qry_options": base.get("qry_options") or {},
        "refresh_param": "refresh",
    }

    derived: dict[str, Any] = {
        "variables": variables_dump,
        "variables_supported": variables_result["variables_supported"],
        "structural_placeholders": variables_result["structural_placeholders"],
        "effective_cond_definition": effective_cond_definition,
        "capabilities": capabilities,
        "links": {"columns": columns_link, "vocabulary": vocabulary_link},
        "warnings": variables_result["warnings"],
    }
    if "variables_error" in variables_result:
        derived["variables_error"] = variables_result["variables_error"]

    payload, redacted = redact_payload(base, grants)

    return {**payload, "derived": derived, "redacted": redacted}
