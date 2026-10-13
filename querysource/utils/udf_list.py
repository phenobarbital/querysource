"""Single source of the relative-date keyword allowlist (FEAT-167, spec §3 M2).

``querysource/types/validators.pyx`` and ``querysource/utils/functions.pyx``
both read the list from here, so ``is_udf()`` and ``to_udf()`` can never
disagree. The environment variable ``UDF_LIST`` (comma-separated) replaces the
default per deployment — same semantics as ``validators.pyx::_env_list``.
"""
from __future__ import annotations

import os

ENV_VAR: str = "UDF_LIST"

DEFAULT_UDF_LIST: tuple[str, ...] = (
    "CURRENT_YEAR",
    "CURRENT_MONTH",
    "TODAY",
    "YESTERDAY",
    "LAST_YEAR",
    "FDOM",
    "LDOM",
    "FDOW",
    "LDOW",
    "LDOPM",
)


def udf_list() -> list[str]:
    """Return the effective keyword list as a fresh, upper-cased list.

    ``UDF_LIST`` env (comma-separated; items stripped, blanks dropped,
    upper-cased) wins over ``DEFAULT_UDF_LIST`` when set and non-blank.
    """
    raw = os.environ.get(ENV_VAR)
    if raw is None or not str(raw).strip():
        return list(DEFAULT_UDF_LIST)
    return [item.strip().upper() for item in str(raw).split(",") if item.strip()]


def is_udf_keyword(value: object) -> bool:
    """Return True when ``value`` is a str whose stripped, upper-cased form is a keyword."""
    return isinstance(value, str) and value.strip().upper() in udf_list()
