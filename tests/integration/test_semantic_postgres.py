"""Opt-in end-to-end semantic layer test against PostgreSQL (FEAT-175).

Runs only with ``QS_SEMANTIC_IT=1`` and the ``semantic`` extra installed. It uses the
database configured for QuerySource (``querysource.conf.default_dsn``) and creates and
drops one uniquely named table, so point the configuration at a disposable database.
"""
from __future__ import annotations

import os
import uuid

import pytest
from semantic.conftest import *  # noqa: F403  (tests/ is on sys.path; `tests` itself is not a package)

from querysource.auth.principal import QSPrincipal
from querysource.queries.qs import QS

pytestmark = [
    pytest.mark.skipif(os.environ.get("QS_SEMANTIC_IT") != "1", reason="set QS_SEMANTIC_IT=1"),
]
sidemantic = pytest.importorskip("sidemantic")

SLUG = "it.semantic.sales"
TABLE = f"qs_semantic_it_{uuid.uuid4().hex[:8]}"
RESOURCE = f"""\
name: {SLUG}
kind: semantic
semantic:
  format: sidemantic
  definition:
    models:
      - name: orders
        table: {TABLE}
        metrics:
          - name: revenue
            agg: sum
            sql: amount
            description: Total order amount
        dimensions:
          - name: region
            type: categorical
governance:
  row_filters:
    analyst:
      - {{field: region, op: in, value: ["ES", "PT"]}}
"""


@pytest.fixture
async def seeded_table():
    """Create and populate the throwaway table, dropping it afterwards."""
    from asyncdb import AsyncDB

    from querysource.conf import default_dsn

    db = AsyncDB("pg", dsn=default_dsn)
    connection = await db.connection()
    try:
        _, error = await connection.execute(f"CREATE TABLE {TABLE} (region text, amount numeric)")
        assert not error, error
        _, error = await connection.execute(
            f"INSERT INTO {TABLE} VALUES ('ES', 10), ('PT', 5), ('FR', 100)"
        )
        assert not error, error
        yield TABLE
    finally:
        await connection.execute(f"DROP TABLE IF EXISTS {TABLE}")
        await connection.close()


def _analyst() -> QSPrincipal:
    return QSPrincipal(user_id="1", username="analyst", groups=(), roles=("analyst",), tenant_id="public")


async def test_semantic_slug_end_to_end_postgres(semantic_definitions, seeded_table):
    """A semantic slug runs on PostgreSQL and the analyst row filter hides other regions."""
    semantic_definitions.add(query_slug=SLUG, provider="semantic", query_raw=RESOURCE)
    conditions = {"metrics": ["revenue"], "dimensions": ["region"]}

    sql, error = await QS(slug=SLUG, conditions=conditions, principal=_analyst()).dry_run()
    assert error is None
    assert "DATE_TRUNC" not in sql.upper()
    assert "'ES'" in sql and "'PT'" in sql

    qs = QS(slug=SLUG, conditions=conditions, principal=_analyst())
    result = await qs.query()
    rows = result[0] if isinstance(result, (list, tuple)) and result and isinstance(result[0], list) else result
    regions = {row["region"] for row in rows}
    assert regions == {"ES", "PT"}
    assert "FR" not in regions


async def test_semantic_catalog_end_to_end(semantic_definitions):
    """The compiler catalog lists metrics with descriptions for the semantic resource."""
    from querysource.semantic.compilers import get_compiler
    from querysource.semantic.loader import get_loader
    from querysource.semantic.models import CompilationTarget, SemanticResource

    resource = SemanticResource.from_yaml(RESOURCE)
    model = get_loader().resolve(SLUG, "it", resource)
    target = CompilationTarget(driver="pg", dialect="postgres", default_limit=500, max_limit=5000)
    fields = {field.name: field for field in get_compiler("sidemantic").catalog(model, target)}
    assert fields["orders.revenue"].kind == "metric"
    assert fields["orders.revenue"].description == "Total order amount"
    assert fields["orders.region"].kind == "dimension"
    assert fields["orders.revenue"].granted is True
