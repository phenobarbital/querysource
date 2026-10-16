"""Tests for the Sidemantic compile-only compiler adapter (FEAT-175 M4)."""
from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from querysource.semantic.exceptions import (
    SemanticCompilerUnavailable,
    SemanticFieldDenied,
    SemanticRequestError,
)
from querysource.semantic.loader import ModelLoader
from querysource.semantic.models import CompilationTarget, SemanticModelRef, SemanticQuery

MODEL_YAML = """\
models:
  - name: orders
    table: orders
    metrics:
      - name: revenue
        agg: sum
        sql: amount
      - name: order_count
        agg: count
    dimensions:
      - name: region
        type: categorical
      - name: order_date
        type: time
        granularity: day
"""

AMBIGUOUS_YAML = """\
models:
  - name: orders
    table: orders
    metrics:
      - {name: revenue, agg: sum, sql: amount}
    dimensions:
      - {name: region, type: categorical}
  - name: returns
    table: returns
    metrics:
      - {name: refunds, agg: sum, sql: amount}
    dimensions:
      - {name: region, type: categorical}
"""

SECURED_YAML = """\
models:
  - name: orders
    table: orders
    security:
      access: true
    metrics:
      - {name: revenue, agg: sum, sql: amount}
    dimensions:
      - {name: region, type: categorical}
"""


def _ref(tmp_path: Path, text: str, version: str = "v1") -> SemanticModelRef:
    path = tmp_path / "model.yml"
    path.write_text(text)
    return SemanticModelRef(
        slug="s", revision="1", format="sidemantic", source_path=path, model_version=version
    )


def _target(**overrides) -> CompilationTarget:
    values = {"driver": "pg", "dialect": "postgres", "default_limit": 500, "max_limit": 1000}
    values.update(overrides)
    return CompilationTarget(**values)


@pytest.fixture
def compiler(sidemantic_available, tmp_path):
    """Return a compiler with an isolated loader/cache."""
    from querysource.semantic.compilers.sidemantic import SidemanticCompiler

    return SidemanticCompiler(ModelLoader(tmp_path, cache_size=4))


def test_compiler_unavailable_501(monkeypatch):
    real_import = builtins.__import__

    def fake(name, *args, **kwargs):
        if name.startswith("sidemantic"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake)
    from querysource.semantic.compilers import sidemantic as mod

    with pytest.raises(SemanticCompilerUnavailable) as error:
        mod._sidemantic()
    assert error.value.code == 501


def test_get_compiler_unknown_name():
    from querysource.semantic.compilers import get_compiler
    from querysource.semantic.exceptions import SemanticResourceError

    with pytest.raises(SemanticResourceError):
        get_compiler("nope")


def test_compile_only_adapter_never_executes(compiler, tmp_path, monkeypatch):
    import duckdb

    def boom(*args, **kwargs):
        raise AssertionError("duckdb.connect must not be called")

    monkeypatch.setattr(duckdb, "connect", boom)
    from querysource.semantic.compilers.sidemantic import compile_only_adapter_class

    result = compiler.compile(
        _ref(tmp_path, MODEL_YAML), SemanticQuery(metrics=["revenue"], dimensions=["region"]), _target()
    )
    assert "SUM(" in result.sql
    adapter = compile_only_adapter_class()("postgres")
    assert adapter.dialect == "postgres"
    with pytest.raises(RuntimeError, match="compile-only"):
        adapter.execute("select 1")


def test_compiler_qualifies_and_rejects_ambiguous(compiler, tmp_path):
    result = compiler.compile(
        _ref(tmp_path, MODEL_YAML), SemanticQuery(metrics=["revenue"], dimensions=["region"]), _target()
    )
    assert result.metrics == ["orders.revenue"]
    assert result.dimensions == ["orders.region"]
    assert result.parameters["request"]["metrics"] == ["revenue"]

    ambiguous = _ref(tmp_path, AMBIGUOUS_YAML, version="v2")
    with pytest.raises(SemanticRequestError, match="Ambiguous"):
        compiler.compile(ambiguous, SemanticQuery(metrics=["revenue"], dimensions=["region"]), _target())
    with pytest.raises(SemanticRequestError, match="Unknown metric"):
        compiler.compile(ambiguous, SemanticQuery(metrics=["nope"]), _target())


def test_compiler_time_grain_rewrite(compiler, tmp_path):
    ref = _ref(tmp_path, MODEL_YAML)
    result = compiler.compile(
        ref, SemanticQuery(metrics=["revenue"], dimensions=["order_date"], time_grain="month"), _target()
    )
    assert result.dimensions == ["orders.order_date__month"]
    explicit = compiler.compile(
        ref,
        SemanticQuery(metrics=["revenue"], dimensions=["order_date__day"], time_grain="month"),
        _target(),
    )
    assert explicit.dimensions == ["orders.order_date__day"]
    with pytest.raises(SemanticRequestError, match="time dimension"):
        compiler.compile(
            ref, SemanticQuery(metrics=["revenue"], dimensions=["region"], time_grain="month"), _target()
        )


def test_compiler_caps_limit_with_warning(compiler, tmp_path):
    result = compiler.compile(
        _ref(tmp_path, MODEL_YAML), SemanticQuery(metrics=["revenue"], limit=5000), _target(max_limit=100)
    )
    assert "LIMIT 100" in result.sql
    assert any("capped" in warning for warning in result.warnings)


def test_compiler_security_error_maps_to_403(compiler, tmp_path):
    ref = _ref(tmp_path, SECURED_YAML)
    with pytest.raises(SemanticFieldDenied) as error:
        compiler.compile(ref, SemanticQuery(metrics=["revenue"]), _target(user_attributes=None))
    assert error.value.code == 403
    result = compiler.compile(ref, SemanticQuery(metrics=["revenue"]), _target(user_attributes={}))
    assert "SUM(" in result.sql


def test_compiler_catalog(compiler, tmp_path):
    fields = {f.name: f for f in compiler.catalog(_ref(tmp_path, MODEL_YAML), _target())}
    assert fields["orders.revenue"].kind == "metric"
    assert fields["orders.order_date"].granularities
    assert fields["orders.region"].granularities == []


def test_compiler_filters_reach_sql_as_real_columns(compiler, tmp_path):
    from querysource.semantic.models import SemanticFilter

    request = SemanticQuery(
        metrics=["revenue"],
        dimensions=["region"],
        filters=[SemanticFilter(field="region", op="in", value=["ES", "PT"])],
    )
    result = compiler.compile(_ref(tmp_path, MODEL_YAML), request, _target())
    assert '"orders.region"' not in result.sql
    assert "'ES'" in result.sql and "'PT'" in result.sql


@pytest.mark.parametrize(
    "entry",
    ["revenue, (select 1)", "revenue; drop table orders", "pg_sleep(5)", "revenue desc, region", "(select 1)"],
)
def test_compiler_rejects_free_form_order_by(compiler, tmp_path, entry):
    request = SemanticQuery(metrics=["revenue"], dimensions=["region"], order_by=[entry])
    with pytest.raises(SemanticRequestError):
        compiler.compile(_ref(tmp_path, MODEL_YAML), request, _target())


def test_compiler_accepts_catalog_order_by(compiler, tmp_path):
    request = SemanticQuery(metrics=["revenue"], dimensions=["region"], order_by=["revenue desc", "region"])
    result = compiler.compile(_ref(tmp_path, MODEL_YAML), request, _target())
    assert "ORDER BY" in result.sql.upper()
