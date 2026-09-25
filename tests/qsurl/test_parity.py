"""Rust and Lark qsurl back-ends must produce byte-identical IR (spec AC3)."""
from __future__ import annotations

import json

import pytest

import querysource.qsurl as qsurl
from querysource.qsurl import HAS_RUST, QSUrlError, _fallback


def _compact(ir: dict) -> str:
    return json.dumps(ir, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def _check_error(err: QSUrlError, case: dict) -> None:
    exp = case["error"]
    assert (err.kind, err.offset) == (exp["kind"], exp["offset"])
    if case.get("message_match", "exact") == "exact":
        assert err.message == exp["message"]
    else:
        assert err.message.split("; expected")[0] == exp["message"].split("; expected")[0]


def test_lark_matches_corpus(corpus: list[dict]) -> None:
    """Every case through the Lark fallback."""
    failures: list[str] = []
    for case in corpus:
        try:
            if "error" in case:
                try:
                    _fallback.parse(case["input"])
                except QSUrlError as err:
                    _check_error(err, case)
                else:
                    raise AssertionError("expected QSUrlError, none raised")
            else:
                ir = _fallback.parse(case["input"])
                assert _compact(ir) == case["ir_json"]
        except AssertionError as exc:
            failures.append(f"{case['id']}: {exc}")
    assert not failures, "\n".join(failures)


@pytest.mark.skipif(not HAS_RUST, reason="qsurl Rust extension not installed")
def test_rust_matches_corpus(corpus: list[dict]) -> None:
    """Every case through the Rust extension; compares the raw JSON string."""
    failures: list[str] = []
    for case in corpus:
        try:
            if "error" in case:
                try:
                    qsurl._rs.parse(case["input"])  # noqa: SLF001 - intentional back-end probe
                except ValueError as err:
                    parsed = QSUrlError.from_json(str(err.args[0]))
                    _check_error(parsed, case)
                else:
                    raise AssertionError("expected ValueError, none raised")
            else:
                raw = qsurl._rs.parse(case["input"])  # noqa: SLF001 - intentional back-end probe
                assert json.dumps(json.loads(raw), separators=(",", ":"), ensure_ascii=False, sort_keys=True) == case["ir_json"]
        except AssertionError as exc:
            failures.append(f"{case['id']}: {exc}")
    assert not failures, "\n".join(failures)
