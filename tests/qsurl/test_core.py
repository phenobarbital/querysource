"""Unit tests for querysource.qsurl package core (TASK-765)."""
from __future__ import annotations

import importlib

import pytest

from querysource.exceptions import QueryException
from querysource.qsurl import QSUrlError, ResidualPlan
from querysource.qsurl import capabilities as caps


def test_all_is_rust_declaration_order():
    assert caps.ALL[:3] == ("select", "alias", "filter") and caps.ALL[-1] == "distinct"


def test_validate_rejects_unknown():
    with pytest.raises(ValueError):
        caps.validate(frozenset({"select", "foo"}))


def test_qsurlerror_roundtrip_and_code():
    err = QSUrlError("parse", "boom", offset=3, expected=["x"], pointer="ab\n   ^")
    assert QSUrlError.from_json(str(err)).to_dict() == err.to_dict()
    assert err.code == 400 and isinstance(err, QueryException)


def test_from_json_lower_defaults():
    err = QSUrlError.from_json('{"kind":"lower","message":"m"}')
    assert err.to_dict() == {
        "kind": "lower",
        "offset": 0,
        "message": "m",
        "found": None,
        "expected": [],
        "pointer": "",
    }


def test_residualplan_is_empty():
    assert ResidualPlan().is_empty()
    assert not ResidualPlan(limit=1).is_empty()


def test_force_fallback_env(monkeypatch):
    monkeypatch.setenv("QSURL_FORCE_FALLBACK", "1")
    import querysource.qsurl as qsurl_module

    reloaded = importlib.reload(qsurl_module)
    try:
        assert not reloaded.HAS_RUST
    finally:
        monkeypatch.delenv("QSURL_FORCE_FALLBACK", raising=False)
        importlib.reload(qsurl_module)
