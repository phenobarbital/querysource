"""FEAT-180: docs/FILTER_OPERATORS.md documents every partial-matching operator (spec AC12)."""

from pathlib import Path

import pytest

from querysource.parsers.partial_matching import PARTIAL_MATCH_OPERATORS

DOC = Path(__file__).resolve().parents[1] / "docs" / "FILTER_OPERATORS.md"
HEADINGS = ("## Operators", "## Rendering per dialect", "## Reserved names", "## Errors", "## qsurl")


def test_headings():
    text = DOC.read_text(encoding="utf-8")
    assert [h for h in HEADINGS if h not in text] == []


@pytest.mark.parametrize("op", list(PARTIAL_MATCH_OPERATORS))
def test_operator_documented(op):
    assert f"`{op}`" in DOC.read_text(encoding="utf-8")
