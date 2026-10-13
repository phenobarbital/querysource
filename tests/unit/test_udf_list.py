"""FEAT-167 TASK-872 — single UDF_LIST source and allowlist-only to_udf()."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from querysource.types.validators import udf_keywords
from querysource.utils.functions import to_udf
from querysource.utils.udf_list import DEFAULT_UDF_LIST, is_udf_keyword, udf_list

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED = [
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
]


def test_default_list_has_ten_keywords() -> None:
    """DEFAULT_UDF_LIST, udf_list() and the Cython udf_keywords() agree (AC2)."""
    assert list(DEFAULT_UDF_LIST) == EXPECTED
    assert udf_list() == EXPECTED
    assert udf_keywords() == EXPECTED


@pytest.mark.parametrize("name", ["TODAY", "today", " Today ", "fdom", "LDOW", "ldopm"])
def test_to_udf_resolves_keywords(name: str) -> None:
    """Keywords resolve case-insensitively and return a non-empty string/int."""
    result = to_udf(name)
    assert isinstance(result, str)
    assert result


@pytest.mark.parametrize("name", ["midnight", "now", "uri_exists", "print", "days_ago", "", "to_udf"])
def test_to_udf_rejects_non_keywords(name: str) -> None:
    """Any name outside UDF_LIST raises KeyError — never executes (AC2)."""
    with pytest.raises(KeyError):
        to_udf(name)


def test_is_udf_keyword() -> None:
    """Recognise normalized keyword strings without accepting arbitrary names."""
    assert is_udf_keyword(" ldopm ")
    assert not is_udf_keyword("midnight")
    assert not is_udf_keyword(None)


def test_env_override_applies_to_both_sides() -> None:
    """UDF_LIST env is honoured by udf_keywords() AND to_udf() in a fresh interpreter."""
    env = os.environ.copy()
    env["UDF_LIST"] = "today, fdom"
    code = (
        "from querysource.types.validators import udf_keywords\n"
        "from querysource.utils.functions import to_udf\n"
        "print(udf_keywords())\n"
        "try:\n    to_udf('LDOM'); print('resolved')\nexcept KeyError:\n    print('KeyError')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip().splitlines()
    assert out[-2] == "['TODAY', 'FDOM']"
    assert out[-1] == "KeyError"
