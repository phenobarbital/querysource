"""FEAT-148 TASK-737 — slug visibility service."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from querysource.auth import slug_visibility as sv


def _request(app: dict | None = None, extra: dict | None = None):
    req = MagicMock()
    req.app = app or {}
    data = dict(extra or {})
    req.get.side_effect = data.get
    return req


def _session(userinfo: dict):
    s = MagicMock()
    s.get.side_effect = lambda k, d=None: userinfo if k == "session" else d
    return s


def test_normalize_programs():
    """Test normalize_programs with various inputs."""
    # Test None
    assert sv.normalize_programs(None) == ()
    
    # Test string
    assert sv.normalize_programs("test") == ("test",)
    
    # Test list of strings
    assert sv.normalize_programs(["test1", "test2"]) == ("test1", "test2")
    
    # Test tuple of strings
    assert sv.normalize_programs(("test1", "test2")) == ("test1", "test2")
    
    # Test objects with slug attribute
    obj1 = MagicMock()
    obj1.slug = "test1"
    obj2 = MagicMock()
    obj2.slug = "test2"
    assert sv.normalize_programs([obj1, obj2]) == ("test1", "test2")
    
    # Test objects with name attribute only (SimpleNamespace has no auto-vivified
    # 'slug' attribute, unlike a bare MagicMock() — required to actually exercise
    # the getattr(item, 'name', None) fallback path).
    obj1 = SimpleNamespace(name="test1")
    obj2 = SimpleNamespace(name="test2")
    assert sv.normalize_programs([obj1, obj2]) == ("test1", "test2")
    
    # Test dicts with slug/name keys
    assert sv.normalize_programs([{"slug": "test1"}, {"name": "test2"}]) == ("test1", "test2")
    
    # Test mixed case and duplicates
    assert sv.normalize_programs(["TEST1", "test1", "Test2"]) == ("test1", "test2")
    
    # Test empty values
    assert sv.normalize_programs(["", None, "test"]) == ("test",)


async def test_resolve_principal_kinds():
    """Test resolve_principal with different principal kinds."""
    # Test NONE principal (no session, no sessionless authz)
    with patch('querysource.auth.slug_visibility.QS_PBAC_ALLOW_SESSIONLESS_AUTHZ', False):
        principal = await sv.resolve_principal(_request(), None)
        assert principal.kind == sv.PrincipalKind.NONE
        assert principal.userinfo == {}
        assert principal.groups == ()
        assert principal.programs == ()
        assert principal.session is None
    
    # Test AUTHZ principal (sessionless authz enabled)
    with patch('querysource.auth.slug_visibility.QS_PBAC_ALLOW_SESSIONLESS_AUTHZ', True):
        request = _request(extra={'authz_backend': 'test_backend'})
        principal = await sv.resolve_principal(request, None)
        assert principal.kind == sv.PrincipalKind.AUTHZ
        assert principal.userinfo['username'] == 'authz:test_backend'
        assert 'authorized' in principal.groups
        assert 'test_backend' in principal.groups
        assert principal.session is None
    
    # Test SUPERUSER principal
    session = _session({'superuser': True, 'groups': ['admin'], 'programs': ['prog1']})
    principal = await sv.resolve_principal(_request(), session)
    assert principal.kind == sv.PrincipalKind.SUPERUSER
    assert principal.userinfo['superuser'] is True
    assert 'admin' in principal.groups
    assert 'prog1' in principal.programs
    
    # Test PROGRAMS principal
    session = _session({'groups': ['user'], 'programs': ['prog1', 'prog2']})
    principal = await sv.resolve_principal(_request(), session)
    assert principal.kind == sv.PrincipalKind.PROGRAMS
    assert principal.programs == ('prog1', 'prog2')
    assert 'user' in principal.groups
    
    # Test NO_PROGRAMS principal
    session = _session({'groups': ['user']})
    principal = await sv.resolve_principal(_request(), session)
    assert principal.kind == sv.PrincipalKind.NO_PROGRAMS
    assert principal.programs == ()


def test_program_predicate_legacy_and_tenant():
    """Test build_program_predicate for legacy and tenant stores."""
    # Test NONE principal -> deny_all
    principal = sv.Principal(sv.PrincipalKind.NONE, {}, (), ())
    store = sv.legacy_store()
    predicate = sv.build_program_predicate(principal, store)
    assert predicate.deny_all is True
    
    # Test NO_PROGRAMS principal -> deny_all
    principal = sv.Principal(sv.PrincipalKind.NO_PROGRAMS, {}, (), ())
    predicate = sv.build_program_predicate(principal, store)
    assert predicate.deny_all is True
    
    # Test SUPERUSER principal -> allow all
    principal = sv.Principal(sv.PrincipalKind.SUPERUSER, {}, (), ())
    predicate = sv.build_program_predicate(principal, store)
    assert predicate.deny_all is False
    assert predicate.sql == ""
    assert predicate.args == ()
    
    # Test AUTHZ principal -> allow all
    principal = sv.Principal(sv.PrincipalKind.AUTHZ, {}, (), ())
    predicate = sv.build_program_predicate(principal, store)
    assert predicate.deny_all is False
    assert predicate.sql == ""
    assert predicate.args == ()
    
    # Test PROGRAMS principal with legacy store
    principal = sv.Principal(sv.PrincipalKind.PROGRAMS, {}, (), ('prog1', 'prog2'))
    predicate = sv.build_program_predicate(principal, store, param_index=2)
    assert predicate.deny_all is False
    assert predicate.sql == 'lower("program_slug") = ANY($2::text[])'
    assert 'prog1' in predicate.args[0]
    assert 'prog2' in predicate.args[0]
    assert 'default' in predicate.args[0]
    
    # Test PROGRAMS principal with tenant store (matching tenant)
    tenant_store = sv.DescribeStore(
        schema="test", table="test", has_program_slug=False, tenant="prog1"
    )
    predicate = sv.build_program_predicate(principal, tenant_store)
    assert predicate.deny_all is False
    
    # Test PROGRAMS principal with tenant store (non-matching tenant)
    tenant_store = sv.DescribeStore(
        schema="test", table="test", has_program_slug=False, tenant="prog3"
    )
    predicate = sv.build_program_predicate(principal, tenant_store)
    assert predicate.deny_all is True


def test_is_admin_groups_config():
    """Test is_admin with different group configurations."""
    # Test SUPERUSER principal
    principal = sv.Principal(sv.PrincipalKind.SUPERUSER, {}, (), ())
    assert sv.is_admin(principal) is True
    
    # Test admin group match
    with patch('querysource.auth.slug_visibility.QS_DESCRIBE_ADMIN_GROUPS', ['admin']):
        principal = sv.Principal(sv.PrincipalKind.PROGRAMS, {}, ('admin',), ())
        assert sv.is_admin(principal) is True
    
    # Test no admin group match
    with patch('querysource.auth.slug_visibility.QS_DESCRIBE_ADMIN_GROUPS', ['admin']):
        principal = sv.Principal(sv.PrincipalKind.PROGRAMS, {}, ('user',), ())
        assert sv.is_admin(principal) is False
    
    # Test case insensitive match
    with patch('querysource.auth.slug_visibility.QS_DESCRIBE_ADMIN_GROUPS', ['admin']):
        principal = sv.Principal(sv.PrincipalKind.PROGRAMS, {}, ('ADMIN',), ())
        assert sv.is_admin(principal) is True


async def test_filter_visible_fallback_only_on_remainder():
    """Test filter_visible with fallback action applied only to denied items."""
    # Mock evaluator
    evaluator = MagicMock()
    
    # Mock filter_resources to return some denied items
    primary_result = MagicMock()
    primary_result.allowed = ['slug1', 'slug2']
    primary_result.denied = ['slug3', 'slug4']
    evaluator.filter_resources.return_value = primary_result
    
    fallback_result = MagicMock()
    fallback_result.allowed = ['slug3']  # Only one of the denied items is allowed
    fallback_result.denied = ['slug4']
    
    # Set up side effect to return different results for primary vs fallback
    def filter_resources_side_effect(**kwargs):
        if kwargs['action'] == 'primary':
            return primary_result
        else:  # fallback
            return fallback_result
    
    evaluator.filter_resources.side_effect = filter_resources_side_effect
    
    request = _request({'security': MagicMock(), 'policy_evaluator': evaluator})
    principal = sv.Principal(sv.PrincipalKind.PROGRAMS, {}, (), ('prog1',))
    
    result = await sv.filter_visible(
        request, principal, ['slug1', 'slug2', 'slug3', 'slug4'],
        primary_action='primary', fallback_action='fallback'
    )
    
    # Should include allowed from both primary and fallback, preserving order
    assert result == ['slug1', 'slug2', 'slug3']
    
    # Check that filter_resources was called twice
    assert evaluator.filter_resources.call_count == 2


async def test_filter_visible_fail_closed_on_error():
    """Test filter_visible fails closed on evaluator error."""
    evaluator = MagicMock()
    evaluator.filter_resources.side_effect = Exception("Test error")
    
    request = _request({'security': MagicMock(), 'policy_evaluator': evaluator})
    principal = sv.Principal(sv.PrincipalKind.PROGRAMS, {}, (), ('prog1',))
    
    result = await sv.filter_visible(
        request, principal, ['slug1', 'slug2'],
        primary_action='primary'
    )
    
    # Should return empty list on error
    assert result == []


async def test_filter_visible_pbac_disabled_allows_all():
    """Test filter_visible allows all when PBAC is disabled."""
    request = _request({})  # No security app key
    principal = sv.Principal(sv.PrincipalKind.NONE, {}, (), ())
    
    result = await sv.filter_visible(
        request, principal, ['slug1', 'slug2'],
        primary_action='primary'
    )
    
    # Should return all slugs when PBAC is disabled
    assert result == ['slug1', 'slug2']


async def test_guardian_without_evaluator_denies():
    """Test filter_visible denies all when guardian present but no evaluator."""
    request = _request({'security': MagicMock()})  # Guardian but no evaluator
    principal = sv.Principal(sv.PrincipalKind.PROGRAMS, {}, (), ('prog1',))
    
    result = await sv.filter_visible(
        request, principal, ['slug1', 'slug2'],
        primary_action='primary'
    )
    
    # Should return empty list when no evaluator
    assert result == []


async def test_can_access_fallback_and_coroutine_result():
    """Test can_access with fallback action and coroutine result."""
    # Mock evaluator with coroutine result
    evaluator = MagicMock()
    
    primary_result = MagicMock()
    primary_result.allowed = False
    # Make it a coroutine
    async def async_primary_result():
        return primary_result
    evaluator.check_access.return_value = async_primary_result()
    
    request = _request({'security': MagicMock(), 'policy_evaluator': evaluator})
    principal = sv.Principal(sv.PrincipalKind.PROGRAMS, {}, (), ('prog1',))
    
    result = await sv.can_access(
        request, principal, 'test_slug',
        primary_action='primary', fallback_action='fallback'
    )
    
    # Should return False since we mocked it that way
    assert result is False


async def test_describe_grants_raw_not_implied_by_raw_query_execute():
    """Test describe_grants raw field is not implied by raw_query:execute."""
    # Mock can_access to return False for slug:describe_raw
    with patch('querysource.auth.slug_visibility.can_access', AsyncMock(return_value=False)):
        request = _request()
        principal = sv.Principal(sv.PrincipalKind.PROGRAMS, {}, (), ('prog1',))
        
        grants = await sv.describe_grants(request, principal, 'test_slug')
        
        # raw should be False
        assert grants.raw is False
        # admin should be False (not superuser, no admin groups)
        assert grants.admin is False


def test_legacy_store_never_mutates_meta():
    """Test legacy_store doesn't mutate QueryModel.Meta."""
    # Store original values
    original_schema = sv.QueryModel.Meta.schema
    original_name = sv.QueryModel.Meta.name
    
    store = sv.legacy_store()
    
    # Check values are preserved
    assert store.schema == original_schema
    assert store.table == original_name
    assert store.has_program_slug is True
    assert store.tenant is None
    assert store.loader is not None
    
    # Check Meta is unchanged
    assert sv.QueryModel.Meta.schema == original_schema
    assert sv.QueryModel.Meta.name == original_name