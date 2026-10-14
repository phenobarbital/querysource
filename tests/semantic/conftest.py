"""Shared fixtures for FEAT-175 semantic-layer tests."""
from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_E2E_CONFTEST_PATH = Path(__file__).parents[1] / "e2e" / "conftest.py"

INLINE_RESOURCE_YAML = """\
name: epson.sales
kind: semantic
semantic:
  format: sidemantic
  definition:
    models:
      - name: orders
        table: orders
        metrics:
          - name: revenue
            type: sum
            expression: amount
          - name: order_count
            type: count
        dimensions:
          - name: region
            type: categorical
          - name: order_date
            type: time
            granularity: day
governance:
  row_filters:
    default: []
    analyst:
      - {field: region, op: in, value: ["ES", "PT"]}
"""


@pytest.fixture
def sidemantic_available():
    """Skip tests needing the optional Sidemantic compiler when absent."""
    return pytest.importorskip("sidemantic")


@pytest.fixture
def inline_resource_yaml() -> str:
    """Return a valid inline Sidemantic resource document."""
    return INLINE_RESOURCE_YAML


@pytest.fixture
def semantic_definitions(monkeypatch: pytest.MonkeyPatch, inline_resource_yaml: str):
    """Add the shared semantic resource to the fake definition repository."""
    spec = spec_from_file_location("_semantic_e2e_conftest", _E2E_CONFTEST_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load the shared e2e definitions fixture")
    e2e_conftest = module_from_spec(spec)
    spec.loader.exec_module(e2e_conftest)
    definitions = e2e_conftest.definitions.__wrapped__(monkeypatch)
    definitions.add(query_slug="epson.sales", provider="semantic", query_raw=inline_resource_yaml)
    return definitions


@pytest.fixture
def fake_evaluator(monkeypatch: pytest.MonkeyPatch):
    """Patch evaluator resolution with an evaluator that denies named resources."""
    class Evaluator:
        """Minimal evaluator compatible with ``querysource.auth.enforcement.evaluate``."""

        def __init__(self) -> None:
            """Initialize the mutable resource-deny set."""
            self.deny: set[str] = set()

        async def check_access(self, **kwargs: Any) -> SimpleNamespace:
            """Return an allow decision unless the resource is explicitly denied."""
            resource_name = kwargs.get("resource_name", "")
            return SimpleNamespace(allowed=resource_name not in self.deny)

    evaluator = Evaluator()
    monkeypatch.setattr(
        "querysource.auth.enforcement.resolve_evaluator",
        lambda request, *, detached: (True, evaluator),
    )
    governance = sys.modules.get("querysource.semantic.governance")
    if governance is not None:
        monkeypatch.setattr(governance, "resolve_evaluator", lambda request, *, detached: (True, evaluator))
    return evaluator
