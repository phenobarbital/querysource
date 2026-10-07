"""FEAT-180: Rust operator-table parity with the Python table, and its cargo unit tests."""
from __future__ import annotations

import re

from rust_cargo_runner import ROOT, run_cargo_lib_tests

from querysource.parsers.partial_matching import PARTIAL_MATCH_OPERATORS

RS = ROOT / "rust" / "src" / "partial_match.rs"
ROW_RE = re.compile(
    r'op\("([a-z_]+)", MatchKind::(Like|Regex), (true|false), (true|false), "(%?)", "(%?)", (true|false), (\d+|CONTAINS_MIN_LENGTH)\)'
)


def test_rust_table_matches_python():
    rows = ROW_RE.findall(RS.read_text(encoding="utf-8"))
    assert len(rows) == len(PARTIAL_MATCH_OPERATORS)
    for row, py in zip(rows, PARTIAL_MATCH_OPERATORS.values()):
        name, kind, neg, ins, prefix, suffix, esc, minlen = row
        minlen_v = 3 if minlen == "CONTAINS_MIN_LENGTH" else int(minlen)
        assert (name, kind.lower(), neg == "true", ins == "true", prefix, suffix, esc == "true", minlen_v) == (
            py.name, py.kind, py.negated, py.insensitive, py.prefix, py.suffix, py.escape, py.min_length
        )


def test_partial_match_cargo_units():
    assert run_cargo_lib_tests("partial_match::tests::") >= 1
