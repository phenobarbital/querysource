"""Acceptance tests for the semantic provider."""
from __future__ import annotations

import pytest
from semantic.conftest import *  # noqa: F403  (tests/ is on sys.path; `tests` itself is not a package)

from querysource.exceptions import QueryAccessDenied
from querysource.queries.qs import QS
from querysource.semantic.exceptions import SemanticFieldDenied, SemanticRequestError
from querysource.semantic.models import CompiledQuery

pytestmark = pytest.mark.usefixtures("sidemantic_available")


async def test_provider_dry_run_returns_compiled_sql(semantic_definitions):
    """A semantic slug compiles through the ordinary QS dry-run path."""
    qs = QS(slug="epson.sales", conditions={"metrics": ["revenue"], "dimensions": ["region"]})
    sql, error = await qs.dry_run()
    assert error is None
    assert "SUM(" in sql.upper()


async def test_dry_run_tuple_contract(semantic_definitions):
    """The provider retains the legacy SQL-and-error tuple interface."""
    qs = QS(slug="epson.sales", conditions={"metrics": ["revenue"], "dimensions": ["region"]})
    sql, error = await qs.dry_run()
    assert sql and error is None
    assert isinstance(qs._qs.compiled, CompiledQuery)


async def test_provider_rejects_unknown_metric_400(semantic_definitions):
    """Unknown metrics surface as semantic request errors with available fields."""
    qs = QS(slug="epson.sales", conditions={"metrics": ["missing"]})
    with pytest.raises(SemanticRequestError, match="available") as exc_info:
        await qs.dry_run()
    assert exc_info.value.code == 400


async def test_provider_limit_capped_with_warning(semantic_definitions):
    """Oversized limits are capped by the compiler and reported as a warning."""
    qs = QS(slug="epson.sales", conditions={"metrics": ["revenue"], "limit": 99999})
    await qs.dry_run()
    assert any("capped" in warning for warning in qs._qs.compiled.warnings)


async def test_provider_enforces_datasource_and_driver_use(semantic_definitions, fake_evaluator):
    """Datasource PBAC runs before model loading or compilation."""
    fake_evaluator.deny.add("default")
    qs = QS(slug="epson.sales", conditions={"metrics": ["revenue"]})
    with pytest.raises(QueryAccessDenied):
        await qs.dry_run()


async def test_provider_checksum_includes_model_version():
    """Compiled model revisions contribute to the provider cache checksum."""
    from querysource.providers.semantic import semanticProvider

    provider = object.__new__(semanticProvider)
    provider._query = "SELECT 1"
    provider.compiled = CompiledQuery(
        sql="SELECT 1", connection="default", dialect="postgres", model_version="one"
    )
    first = provider.checksum()
    provider.compiled.model_version = "two"
    assert provider.checksum() != first


async def test_multiqs_semantic_child_without_principal_is_denied_on_secured_model(
    semantic_definitions, inline_resource_yaml: str
):
    """A secured semantic child without a principal is denied before SQL exists."""
    secured_resource = inline_resource_yaml.replace(
        "        table: orders", "        table: orders\n        security:\n          access: true"
    )
    semantic_definitions.add(query_slug="epson.secured", provider="semantic", query_raw=secured_resource)
    qs = QS(slug="epson.secured", conditions={"metrics": ["revenue"]})
    with pytest.raises(SemanticFieldDenied) as exc_info:
        await qs.dry_run()
    assert exc_info.value.code == 403


def _analyst():
    from querysource.auth.principal import QSPrincipal

    return QSPrincipal(user_id="1", username="a", groups=(), roles=("analyst",), tenant_id="public")


async def test_governance_row_filter_reaches_sql_for_role_only(semantic_definitions):
    """Role row filters are AND-ed into the SQL for matching roles and ignored otherwise."""
    conditions = {"metrics": ["revenue"], "dimensions": ["region"]}
    sql, _ = await QS(slug="epson.sales", conditions=conditions, principal=_analyst()).dry_run()
    assert "'ES'" in sql and "'PT'" in sql
    anonymous_sql, _ = await QS(slug="epson.sales", conditions=conditions).dry_run()
    assert "'ES'" not in anonymous_sql


async def test_invalid_governance_filter_is_500_and_logged(
    semantic_definitions, inline_resource_yaml: str, caplog
):
    """A bad governance filter fails closed with a 500 and logs slug and role."""
    from querysource.semantic.exceptions import SemanticGovernanceError

    bad = inline_resource_yaml.replace('{field: region, op: in, value: ["ES", "PT"]}', "{field: nope, op: '=', value: 1}")
    semantic_definitions.add(query_slug="epson.bad", provider="semantic", query_raw=bad)
    with caplog.at_level("ERROR"):
        with pytest.raises(SemanticGovernanceError) as exc_info:
            await QS(slug="epson.bad", conditions={"metrics": ["revenue"]}, principal=_analyst()).dry_run()
    assert exc_info.value.code == 500
    assert "epson.bad" in caplog.text and "analyst" in caplog.text


async def test_driver_use_denial(semantic_definitions, fake_evaluator):
    """driver:use is enforced independently of datasource:use."""
    fake_evaluator.deny.add("pg")
    with pytest.raises(QueryAccessDenied):
        await QS(slug="epson.sales", conditions={"metrics": ["revenue"]}).dry_run()


async def test_cache_checksum_tracks_model_version_through_qs(semantic_definitions, inline_resource_yaml: str):
    """Identical requests share a checksum; a changed model definition changes it."""
    conditions = {"metrics": ["revenue"]}
    first = QS(slug="epson.sales", conditions=conditions)
    again = QS(slug="epson.sales", conditions=conditions)
    await first.build_provider()
    await again.build_provider()
    assert first._qs.checksum() == again._qs.checksum()

    changed = inline_resource_yaml.replace("name: region", "name: region2")
    semantic_definitions.add(query_slug="epson.changed", provider="semantic", query_raw=changed)
    other = QS(slug="epson.changed", conditions=conditions)
    await other.build_provider()
    assert other._qs.checksum() != first._qs.checksum()
