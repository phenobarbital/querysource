"""Exact qsurl capability declarations per provider (spec §3 Module 5, AC9)."""
from __future__ import annotations

import pytest

from querysource.providers.abstract import BaseProvider
from querysource.providers.cassandra import cassandraProvider
from querysource.providers.pg import pgProvider
from querysource.providers.sql import sqlProvider
from querysource.qsurl import capabilities as caps


@pytest.mark.parametrize(
    "cls,expected,scan",
    [
        (BaseProvider, {"select", "filter", "in_list", "null_check"}, True),
        (
            sqlProvider,
            {"select", "filter", "in_list", "null_check", "alias", "sort", "limit", "offset"},
            True,
        ),
        (
            pgProvider,
            {
                "select",
                "filter",
                "in_list",
                "null_check",
                "alias",
                "sort",
                "limit",
                "offset",
                "text_match",
            },
            True,
        ),
        (cassandraProvider, {"select", "filter", "in_list", "null_check", "limit"}, False),
    ],
)
def test_capability_sets(cls, expected, scan):
    assert cls.capabilities == frozenset(expected)
    assert isinstance(cls.capabilities, frozenset)
    assert cls.residual_scan is scan
    assert caps.validate(cls.capabilities) == cls.capabilities


def test_no_provider_declares_phase1_unsupported():
    for cls in (BaseProvider, sqlProvider, pgProvider, cassandraProvider):
        assert not (cls.capabilities & caps.UNSUPPORTED_PHASE1)
