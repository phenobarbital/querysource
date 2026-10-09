"""FEAT-165: SQL shown in the dialect doc is backed by the parity corpus."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOC = ROOT.parent / "docs" / "QUERYSOURCE_DIALECT.md"
SECTIONS = ("5.3", "5.4", "7.1", "8")


def _load_corpus() -> list[tuple[dict, str]]:
    """Load ``PG_CORPUS`` plus the typed-filter and multi-operator rows of the parity module."""
    spec = importlib.util.spec_from_file_location("_dialect_parity", ROOT / "test_dialect_filter_parity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = list(module.PG_CORPUS)
    for mark in module.test_pg_typed_filters_full_pipeline.pytestmark:
        if mark.name == "parametrize" and tuple(mark.args[0]) == ("conditions", "expected"):
            rows.extend(mark.args[1])
    return rows


def _norm(text: str) -> str:
    """Collapse runs of whitespace so Markdown and corpus SQL compare equal."""
    return re.sub(r"\s+", " ", text).strip()


def _section(text: str, number: str) -> str:
    """Return the markdown between a ``number`` heading and the next heading of the same or higher level."""
    level = "##" if "." not in number else "###"
    match = re.search(rf"^{level} {re.escape(number)}[ .].*$", text, re.MULTILINE)
    assert match, f"section {number} not found"
    rest = text[match.end():]
    stop = re.search(rf"^#{{1,{len(level)}}} ", rest, re.MULTILINE)
    return rest[: stop.start()] if stop else rest


def _sql_cells(section: str) -> list[str]:
    """Return the backticked SQL of the last column of every table row."""
    cells: list[str] = []
    for line in section.splitlines():
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        last = re.split(r"(?<!\\)\|", line.strip().strip("|"))[-1].strip()
        found = re.fullmatch(r"`(.+)`", last)
        if found and found.group(1) != "SQL":
            cells.append(found.group(1))
    return cells


def test_doc_examples_are_verified() -> None:
    expected = {_norm(sql) for _, sql in _load_corpus()}
    text = DOC.read_text(encoding="utf-8")
    cells = [cell for number in SECTIONS for cell in _sql_cells(_section(text, number))]
    assert cells, "no SQL examples found in the documented sections"
    unverified = [cell for cell in cells if _norm(cell) not in expected]
    assert not unverified, f"doc SQL not backed by the parity corpus: {unverified}"
