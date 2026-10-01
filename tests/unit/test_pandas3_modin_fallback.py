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


def _break_modin_import(monkeypatch):
    """Make any modin import fail with a non-ImportError (pandas 3 mismatch)."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "modin" or name.startswith("modin."):
            raise RuntimeError("modin does not support this pandas version")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_operator_modin_runtime_failure_falls_back(monkeypatch, caplog):
    """A non-ImportError failure while importing modin still falls back to pandas."""
    _break_modin_import(monkeypatch)

    operator = Concat({}, backend="modin")

    assert operator._pd is pd
    assert operator._backend == "pandas"
    assert "does not support this pandas version" in caplog.text


def test_modinformat_runtime_failure_raises(monkeypatch):
    """A non-ImportError modin failure surfaces as QueryException."""
    from querysource.outputs.dt.modin import modinFormat

    _break_modin_import(monkeypatch)

    with pytest.raises(QueryException, match=r"querysource\[modin\]"):
        modinFormat()
