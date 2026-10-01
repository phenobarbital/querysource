"""FEAT-157 — only PostgreSQL providers declare the SQL hooks dialect (TASK-824)."""
import pytest

from querysource.interfaces.source_hooks import SourceHooksMixin
from querysource.providers.abstract import BaseProvider
from querysource.providers.db import dbProvider
from querysource.providers.pg import pgProvider
from querysource.providers.sqlserver import sqlserverProvider


def test_provider_dialects():
    assert issubclass(BaseProvider, SourceHooksMixin)
    assert BaseProvider.sql_hooks_dialect is None
    assert pgProvider.sql_hooks_dialect == "postgres"
    assert dbProvider.sql_hooks_dialect == "postgres"
    assert sqlserverProvider.sql_hooks_dialect is None
    pytest.importorskip("google.cloud.bigquery")
    from querysource.providers.bigquery import bigqueryProvider

    assert bigqueryProvider.sql_hooks_dialect is None
