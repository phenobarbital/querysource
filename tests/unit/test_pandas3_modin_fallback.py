"""FEAT-161: modin is optional."""
import sys

import pandas as pd
import pytest

from querysource.exceptions import QueryException
from querysource.queries.multi.operators.Concat import Concat


def test_operator_modin_fallback(monkeypatch, caplog):
    """Use pandas when the optional modin backend is unavailable."""
    monkeypatch.setitem(sys.modules, "modin.pandas", None)
    monkeypatch.setitem(sys.modules, "modin", None)

    operator = Concat({}, backend="modin")

    assert operator._pd is pd
    assert operator._backend == "pandas"
    assert "Modin backend requested but modin is not installed" in caplog.text


def test_modinformat_missing_raises(monkeypatch):
    """Tell users how to install the optional modin dependency."""
    monkeypatch.setitem(sys.modules, "modin.config", None)
    monkeypatch.setitem(sys.modules, "modin", None)
    from querysource.outputs.dt.modin import modinFormat

    with pytest.raises(QueryException, match=r"querysource\[modin\]"):
        modinFormat()
