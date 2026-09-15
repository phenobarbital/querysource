"""Curated relative-date keyword vocabulary (FEAT-148).

Documents keywords accepted as condition *values* (resolved by ``to_udf``), PostgreSQL
constants, and an informational set of date helpers. Served at GET /api/v1/queries/vocabulary.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)

VOCABULARY_VERSION: str = "1.0"


class KeywordEntry(BaseModel):
    """A relative-date keyword accepted as a condition value."""

    name: str
    category: Literal["date"] = "date"
    returns: str
    description: str | None = None


class FunctionArg(BaseModel):
    """One documented argument of an informational helper."""

    name: str
    default: Any = None


class FunctionEntry(BaseModel):
    """Informational date helper (not invocable from HTTP conditions in v1)."""

    name: str
    args: list[FunctionArg]
    description: str
    invocable: bool = False


KEYWORD_REGISTRY: dict[str, KeywordEntry] = {
    "TODAY": KeywordEntry(
        name="TODAY",
        category="date",
        returns="date-string",
        description="Current date in the server timezone, formatted MM/DD/YYYY.",
    ),
    "YESTERDAY": KeywordEntry(
        name="YESTERDAY",
        category="date",
        returns="date-string",
        description="Previous day, formatted YYYY-MM-DD.",
    ),
    "FDOM": KeywordEntry(
        name="FDOM",
        category="date",
        returns="date-string",
        description="First day of the current month, YYYY-MM-DD.",
    ),
    "LDOM": KeywordEntry(
        name="LDOM",
        category="date",
        returns="date-string",
        description="Last day of the current month, YYYY-MM-DD.",
    ),
    "LAST_YEAR": KeywordEntry(
        name="LAST_YEAR",
        category="date",
        returns="date-string",
        description="Same calendar day one year ago, YYYY-MM-DD.",
    ),
    "CURRENT_YEAR": KeywordEntry(
        name="CURRENT_YEAR",
        category="date",
        returns="integer",
        description="Current four-digit year.",
    ),
    "CURRENT_MONTH": KeywordEntry(
        name="CURRENT_MONTH",
        category="date",
        returns="integer",
        description="Current month number (1-12).",
    ),
}

FUNCTION_REGISTRY: tuple[FunctionEntry, ...] = (
    FunctionEntry(
        name="date_diff",
        args=[
            FunctionArg(name="value"),
            FunctionArg(name="diff", default=1),
            FunctionArg(name="mode", default="days"),
            FunctionArg(name="mask", default="%Y-%m-%d"),
            FunctionArg(name="tz", default=None),
        ],
        description="Calculate the difference between two dates.",
        invocable=False,
    ),
    FunctionEntry(
        name="date_sum",
        args=[
            FunctionArg(name="value"),
            FunctionArg(name="diff", default=1),
            FunctionArg(name="mode", default="days"),
            FunctionArg(name="mask", default="%Y-%m-%d"),
            FunctionArg(name="tz", default=None),
        ],
        description="Calculate the sum of two dates.",
        invocable=False,
    ),
    FunctionEntry(
        name="days_ago",
        args=[
            FunctionArg(name="mask", default="%m/%d/%Y"),
            FunctionArg(name="offset", default=1),
        ],
        description="Return a date N days in the past.",
        invocable=False,
    ),
    FunctionEntry(
        name="previous_month",
        args=[
            FunctionArg(name="mask", default="%m/%d/%Y"),
            FunctionArg(name="months", default=1),
        ],
        description="Return the first day of the previous month.",
        invocable=False,
    ),
    FunctionEntry(
        name="fdow",
        args=[
            FunctionArg(name="value", default=None),
            FunctionArg(name="mask", default="%Y-%m-%d"),
            FunctionArg(name="zone", default=None),
        ],
        description="First day of the current week (Monday).",
        invocable=False,
    ),
    FunctionEntry(
        name="ldow",
        args=[
            FunctionArg(name="value", default=None),
            FunctionArg(name="mask", default="%Y-%m-%d"),
            FunctionArg(name="zone", default=None),
        ],
        description="Last day of the current week (Sunday).",
        invocable=False,
    ),
)


def keyword_example(name: str) -> Any:
    """Resolve ``name`` via to_udf now; int stays int, others str; None on any error."""
    from querysource.utils.functions import (
        to_udf,  # compiled module; lazy to keep import side effects local
    )

    try:
        value = to_udf(name.strip())
        if isinstance(value, int):
            return value
        return str(value) if value is not None else None
    except Exception:  # noqa: BLE001
        logger.debug("keyword_example failed for %r", name)
        return None


def build_vocabulary(
    udf_list: list[str], pg_constants: list[str], pg_udfs: list[str]
) -> dict:
    """Build the vocabulary response for the effective keyword/constant lists.

    ``keywords`` follows ``udf_list`` order exactly; names missing from the registry
    are listed with ``description: None``.
    """
    keywords = []
    for name in udf_list:
        entry = KEYWORD_REGISTRY.get(name.upper())
        if entry:
            example = keyword_example(name)
            keywords.append(
                {
                    "name": entry.name,
                    "category": entry.category,
                    "returns": entry.returns,
                    "description": entry.description,
                    "example": example,
                }
            )
        else:
            keywords.append(
                {
                    "name": name,
                    "category": "date",
                    "returns": "unknown",
                    "description": None,
                    "example": None,
                }
            )

    return {
        "version": VOCABULARY_VERSION,
        "case_insensitive": True,
        "keywords": keywords,
        "constants": list(pg_constants),
        "pg_functions": list(pg_udfs),
        "functions": [f.model_dump() for f in FUNCTION_REGISTRY],
        "usage": 'Pass a keyword as a condition value, e.g. {"firstdate": "FDOM", "lastdate": "TODAY"}. Keywords are case-insensitive.',
    }