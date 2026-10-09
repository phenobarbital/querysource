"""httpProvider must fall back to the httpSource *class* when the dialect has no source.

Regression: ``getattr(module, self.dialect, 'httpSource')`` returned the string
``'httpSource'``, so every REST/HTTP query whose dialect has no source module
failed with ``'str' object is not callable``.
"""
import types

import pytest

from querysource.providers import http as http_provider


class FakeSource:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def refresh_proxies(self):
        return None


class DialectSource(FakeSource):
    pass


def make_provider(dialect: str) -> http_provider.httpProvider:
    provider = http_provider.httpProvider.__new__(http_provider.httpProvider)
    provider.dialect = dialect
    provider._definition = None
    provider._conditions = {}
    provider._request = None
    provider._loop = None
    provider._parser = None
    return provider


@pytest.fixture
def fake_modules(monkeypatch):
    """``sources.<dialect>`` / ``plugins.sources.<dialect>`` are missing; the base module has httpSource."""
    base = types.ModuleType("querysource.providers.sources.http")
    base.httpSource = FakeSource
    base.custom = DialectSource

    def fake_import(name, package=None):
        if name == "querysource.providers.sources.http":
            return base
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(http_provider, "import_module", fake_import)
    monkeypatch.setattr(http_provider.importlib, "import_module", fake_import)
    return base


async def test_unknown_dialect_uses_base_http_source(fake_modules):
    provider = make_provider("venu")
    await provider.prepare_connection()
    assert type(provider._source) is FakeSource


async def test_dialect_class_in_module_is_still_preferred(fake_modules):
    provider = make_provider("custom")
    await provider.prepare_connection()
    assert type(provider._source) is DialectSource
