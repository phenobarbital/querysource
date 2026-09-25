"""Packaging: grammar ships as package data; the extension imports when built (spec AC17)."""
from __future__ import annotations

import sys
from importlib.resources import files
from pathlib import Path

import pytest

import querysource.qsurl as qsurl

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - project requires-python >=3.10 in pyproject.toml
    import tomli as tomllib


def test_grammar_is_package_data():
    assert (files("querysource.qsurl") / "grammar.lark").is_file()


@pytest.mark.skipif(not qsurl.HAS_RUST, reason="qsurl Rust extension not installed")
def test_extension_exposes_parse_and_requires():
    assert callable(qsurl._rs.parse)  # noqa: SLF001 - intentional back-end probe
    assert callable(qsurl._rs.requires)  # noqa: SLF001 - intentional back-end probe


def test_package_data_declared_in_pyproject():
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    package_data = data["tool"]["setuptools"]["package-data"]
    entry = package_data["querysource.qsurl"]
    assert set(entry) == {"*.so", "*.pyd", "*.lark"}
