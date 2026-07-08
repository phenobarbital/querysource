"""Integration tests for cond_definition wiring in raw_query() (FEAT-103).

Prior to this fix, providers called
`safe_format_map_validated(sql, self._conditions, {})` with a hard-coded
empty dict, so the `cond_definition` type hints saved on a slug's
QueryModel (e.g. {"ids": "ARRAY"}) never reached the Rust validator and
had no effect on substitution.

These tests exercise the real `defaultProvider.raw_query()` (and, as a
cross-provider sanity check, `cassandraProvider.get_raw_query()`) against
a fixture QueryModel carrying `cond_definition`, verifying:
  - the "array" type hint now takes effect (values pass through unquoted,
    e.g. for a pre-rendered `IN (...)` list), fixing Bug 1;
  - hints persisted in upper case (as the Navigator frontend does) are
    honored the same as lower case, fixing Bug 2;
  - the injection check still runs even when the "array" hint applies.

`BaseProvider.__init__` requires a running asyncio loop (it calls
`asyncio.get_running_loop()`), so provider-instantiating tests are
`async def` — `asyncio_mode = auto` (pytest.ini) runs them under one.
"""
import sys
import types
import logging as _stdlib_logging

import pytest

# Importing `querysource.providers.*` pulls in `navconfig.logging`, whose
# module-level init does a live socket check against LOGSTASH_HOST. In
# offline/sandboxed environments (no route to the internal logstash host)
# that raises `socket.gaierror` and aborts the import entirely. Only when
# the real import fails do we substitute a minimal stand-in — in a normal
# dev/CI environment (where the logstash host resolves or is disabled) this
# has no effect and the real navconfig.logging module is used untouched.
try:
    import navconfig.logging  # noqa: F401
except Exception:
    _fake = types.ModuleType('navconfig.logging')
    _fake.logging = _stdlib_logging
    sys.modules['navconfig.logging'] = _fake

from querysource.providers.default import defaultProvider  # noqa: E402
from querysource.providers.cassandra import cassandraProvider  # noqa: E402
from querysource.providers.abstract import BaseProvider  # noqa: E402
from querysource.models import QueryModel  # noqa: E402


def _slug_definition(query_raw: str, cond_definition: dict | None = None) -> QueryModel:
    return QueryModel(
        query_slug='test_slug',
        query_raw=query_raw,
        is_raw=True,
        cond_definition=cond_definition or {},
    )


# ---------------------------------------------------------------------------
# Bug 1 — cond_definition reaches the Rust validator
# ---------------------------------------------------------------------------


async def test_default_provider_array_hint_renders_prerendered_list():
    """Fixture scenario from the bug report: IN ({ids}) + ARRAY hint +
    a pre-rendered '(...)' value substitutes verbatim (unquoted)."""
    definition = _slug_definition(
        "SELECT * FROM t WHERE id IN ({ids})",
        cond_definition={"ids": "ARRAY"},
    )
    provider = defaultProvider(
        slug='test_slug',
        qstype='slug',
        definition=definition,
        conditions={"ids": "'a','b','c'"},
    )
    sql = provider.raw_query(provider._query)
    assert sql == "SELECT * FROM t WHERE id IN ('a','b','c')"


async def test_default_provider_without_cond_definition_uses_generic_quoting():
    """Regression: no cond_definition (default {}) behaves like before the
    fix — a bare placeholder still falls through to generic quoting."""
    definition = _slug_definition("SELECT * FROM t WHERE tag = {tag}")
    provider = defaultProvider(
        slug='test_slug',
        qstype='slug',
        definition=definition,
        conditions={"tag": "hello"},
    )
    sql = provider.raw_query(provider._query)
    assert sql == "SELECT * FROM t WHERE tag = 'hello'"


async def test_cassandra_provider_also_wires_cond_definition():
    """Cross-provider check: the same _get_cond_definition() wiring is used
    by every provider's raw_query/get_raw_query, not just Postgres."""
    definition = _slug_definition(
        "SELECT * FROM t WHERE id IN ({ids})",
        cond_definition={"ids": "array"},
    )
    provider = cassandraProvider(
        slug='test_slug',
        qstype='slug',
        definition=definition,
        conditions={"ids": "'a','b','c'"},
    )
    sql = provider.get_raw_query(provider._query)
    assert sql == "SELECT * FROM t WHERE id IN ('a','b','c')"


# ---------------------------------------------------------------------------
# Bug 2 — cond_definition hints are case-insensitive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hint", ["ARRAY", "array", "Array"])
async def test_array_hint_matches_regardless_of_case(hint):
    definition = _slug_definition(
        "SELECT * FROM t WHERE id IN ({ids})",
        cond_definition={"ids": hint},
    )
    provider = defaultProvider(
        slug='test_slug',
        qstype='slug',
        definition=definition,
        conditions={"ids": "'a','b','c'"},
    )
    sql = provider.raw_query(provider._query)
    assert sql == "SELECT * FROM t WHERE id IN ('a','b','c')"


# ---------------------------------------------------------------------------
# Security — injection checks still run under an "array" hint
# ---------------------------------------------------------------------------


async def test_array_hint_does_not_bypass_injection_check():
    definition = _slug_definition(
        "SELECT * FROM t WHERE id IN ({ids})",
        cond_definition={"ids": "ARRAY"},
    )
    provider = defaultProvider(
        slug='test_slug',
        qstype='slug',
        definition=definition,
        conditions={"ids": "'a'); DROP TABLE t;--"},
    )
    with pytest.raises(Exception):
        provider.raw_query(provider._query)


# ---------------------------------------------------------------------------
# _get_cond_definition() helper — handles both QueryModel and plain-dict
# definitions, and normalizes a missing/None definition to {}.
# ---------------------------------------------------------------------------


async def test_get_cond_definition_from_query_model():
    definition = _slug_definition("SELECT 1", cond_definition={"x": "INTEGER"})
    provider = defaultProvider(slug='test_slug', qstype='slug', definition=definition, conditions={})
    assert provider._get_cond_definition() == {"x": "INTEGER"}


class _ConcreteProvider(BaseProvider):
    """Minimal concrete subclass — BaseProvider.query() is abstract, so it
    can't be instantiated (even via __new__) without a real implementation.
    Only used here to exercise _get_cond_definition() in isolation, without
    going through __init__ (and its event-loop/parser requirements)."""

    async def query(self):
        raise NotImplementedError


def test_get_cond_definition_from_plain_dict():
    provider = _ConcreteProvider.__new__(_ConcreteProvider)
    provider._definition = {"cond_definition": {"x": "STRING"}}
    assert provider._get_cond_definition() == {"x": "STRING"}


def test_get_cond_definition_defaults_to_empty_dict():
    provider = _ConcreteProvider.__new__(_ConcreteProvider)
    provider._definition = None
    assert provider._get_cond_definition() == {}
