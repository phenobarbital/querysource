"""docs/QSURL.md keeps its structure and its examples parse (spec AC20)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from querysource.qsurl import QSUrlError, parse

DOC = Path(__file__).resolve().parents[2] / "docs" / "QSURL.md"
HEADINGS = (
    "## Quick start",
    "## Grammar",
    "## Operators",
    "## Pipeline operators",
    "## Intermediate representation (IR)",
    "## Pushdown and residual evaluation",
    "## Cost guard",
    "## Errors",
    "## Python API",
    "## Limits",
)


def test_required_headings():
    text = DOC.read_text(encoding="utf-8")
    assert [h for h in HEADINGS if h not in text] == []


def test_qsurl_examples_parse():
    blocks = re.findall(r"```qsurl\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
    assert blocks, "document at least one ```qsurl example"
    for block in blocks:
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.endswith("# error"):
                query = line[: -len("# error")].strip()
                with pytest.raises(QSUrlError):
                    parse(query)
            else:
                parse(line)  # must not raise
