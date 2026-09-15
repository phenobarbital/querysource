"""Define tenant persistence validation and runtime compatibility regression contracts."""
import pytest

from querysource.models import QueryModel
from querysource.tenant_models import TenantQueryDefinition


@pytest.mark.asyncio
async def test_tenant_columns_exclude_program_slug() -> None:
    """tenant columns exclude program slug."""
    # Verify that TenantQueryDefinition does not have program_slug
    tenant_def = TenantQueryDefinition(
        query_slug="test_query",
        program_id=1,
    )
    assert not hasattr(tenant_def, 'program_slug')

    # AC-3: program_slug must be rejected before model construction, not
    # silently accepted/ignored as an extra field.
    with pytest.raises(TypeError):
        TenantQueryDefinition(
            query_slug="test_query",
            program_id=1,
            program_slug="tenant1",
        )

    # Verify that QueryModel has program_slug
    query_model = QueryModel(
        query_slug="test_query",
        program_id=1,
        program_slug="default",
    )
    assert hasattr(query_model, 'program_slug')


@pytest.mark.asyncio
async def test_defaults_and_required_program_id() -> None:
    """defaults and required program id."""
    # Test that program_id is required and defaults to 1
    tenant_def = TenantQueryDefinition(
        query_slug="test_query",
    )
    assert tenant_def.program_id == 1
    
    # Test that program_id can be explicitly set
    tenant_def = TenantQueryDefinition(
        query_slug="test_query",
        program_id=2,
    )
    assert tenant_def.program_id == 2


@pytest.mark.asyncio
async def test_json_array_datetime_validation() -> None:
    """json array datetime validation."""
    # Test JSON fields
    tenant_def = TenantQueryDefinition(
        query_slug="test_query",
        params={"key": "value"},
        attributes={"attr": "value"},
        conditions={"cond": "value"},
        cond_definition={"def": "value"},
        filtering={"filter": "value"},
        qry_options={"opt": "value"},
        cache_options={"cache": "value"},
        dwh_info={"dwh": "value"},
        dwh_scheduler={"sched": "value"},
    )
    assert tenant_def.params == {"key": "value"}
    assert tenant_def.attributes == {"attr": "value"}
    assert tenant_def.conditions == {"cond": "value"}
    assert tenant_def.cond_definition == {"def": "value"}
    assert tenant_def.filtering == {"filter": "value"}
    assert tenant_def.qry_options == {"opt": "value"}
    assert tenant_def.cache_options == {"cache": "value"}
    assert tenant_def.dwh_info == {"dwh": "value"}
    assert tenant_def.dwh_scheduler == {"sched": "value"}
    
    # Test array fields
    tenant_def = TenantQueryDefinition(
        query_slug="test_query",
        fields=["field1", "field2"],
        ordering=["order1", "order2"],
        grouping=["group1", "group2"],
    )
    assert tenant_def.fields == ["field1", "field2"]
    assert tenant_def.ordering == ["order1", "order2"]
    assert tenant_def.grouping == ["group1", "group2"]
    
    # Test datetime fields
    import datetime
    now = datetime.datetime.now(datetime.UTC)
    tenant_def = TenantQueryDefinition(
        query_slug="test_query",
        created_at=now,
    )
    assert tenant_def.created_at == now

    # updated_at uses encoder=rigth_now (matching QueryModel), which always
    # rewrites the value to datetime.now() regardless of what was passed —
    # verify that timestamp-factory parity with the legacy model, not a
    # pass-through of the supplied value.
    assert isinstance(tenant_def.updated_at, datetime.datetime)
    assert tenant_def.updated_at != now


@pytest.mark.asyncio
async def test_legacy_model_meta_unchanged() -> None:
    """legacy model meta unchanged."""
    # Verify that QueryModel.Meta is unchanged
    assert hasattr(QueryModel, 'Meta')
    assert QueryModel.Meta.driver == 'pg'
    assert QueryModel.Meta.name == 'queries'
    assert QueryModel.Meta.schema == 'public'
    assert QueryModel.Meta.strict
    assert not QueryModel.Meta.frozen
    assert QueryModel.Meta.remove_nulls
