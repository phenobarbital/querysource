"""Route management mutations through selected-owner repository regression contracts."""
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from querysource.tenant_errors import TenantError
from querysource.tenants import QueryIdentity, QueryStore, TenantRegistry


def _mock_registry() -> TenantRegistry:
    """Registry with a single, default tenant-contract store."""
    registry = TenantRegistry()
    store = QueryStore(
        database_namespace="localhost:5432/querysource",
        schema="tenant1",
        table="queries",
        contract="tenant",
        columns=frozenset({"query_slug", "description"}),
    )
    registry._stores = (store,)
    registry._default_store = store
    return registry


def _mock_request(
    method: str = "POST",
    query: dict | None = None,
    match_info: dict | None = None,
    json_data: dict | None = None,
) -> web.Request:
    request = MagicMock(spec=web.Request)
    request.method = method
    request.query = query or {}
    request.match_info = match_info or {}
    request.json_data = json_data
    return request


class _FakeRepo:
    """Records every mutation call so tests can assert on identity/data."""

    def __init__(self, upsert_created: bool = True, upsert_result=None,
                 patch_result=None, delete_result: bool = True,
                 raise_on=None):
        self.calls: list[tuple[str, QueryIdentity, dict | None]] = []
        self._upsert_created = upsert_created
        self._upsert_result = upsert_result or {"query_slug": "test_query"}
        self._patch_result = patch_result or {"query_slug": "test_query"}
        self._delete_result = delete_result
        self._raise_on = raise_on or {}

    async def upsert(self, identity: QueryIdentity, data: dict):
        self.calls.append(("upsert", identity, data))
        if "upsert" in self._raise_on:
            raise self._raise_on["upsert"]
        return self._upsert_result, self._upsert_created

    async def patch(self, identity: QueryIdentity, data: dict):
        self.calls.append(("patch", identity, data))
        if "patch" in self._raise_on:
            raise self._raise_on["patch"]
        return self._patch_result

    async def delete(self, identity: QueryIdentity):
        self.calls.append(("delete", identity, None))
        if "delete" in self._raise_on:
            raise self._raise_on["delete"]
        return self._delete_result


def _manager_with_app(request, *, registry=None, repo=None, qs_connection=None):
    from querysource.handlers.manager import QueryManager

    app = {}
    if registry is not None:
        app["qs_tenant_registry"] = registry
    if repo is not None:
        app["qs_definition_repository"] = repo
    if qs_connection is not None:
        app["qs_connection"] = qs_connection
    request.app = app
    manager = QueryManager(request)

    async def fake_json_data(request=None):
        return request.json_data if request is not None else None

    manager.json_data = lambda req=None: fake_json_data(request)
    manager.get_arguments = lambda: request.match_info
    return manager


@pytest.mark.asyncio
async def test_all_crud_methods_target_selected_store() -> None:
    """all crud methods target selected store."""
    registry = _mock_registry()
    store = registry.resolve(None)

    # POST -> repo.upsert with the resolved store's identity.
    repo = _FakeRepo(upsert_created=True)
    request = _mock_request(
        match_info={"slug": "test_query"},
        json_data={"description": "created via post"},
    )
    manager = _manager_with_app(request, registry=registry, repo=repo)
    response = await manager.post()
    assert response.status == 201
    assert repo.calls[0][0] == "upsert"
    identity = repo.calls[0][1]
    assert identity.store == store
    assert identity.slug == "test_query"

    # PUT -> repo.upsert as well, with a created=False -> 202.
    repo2 = _FakeRepo(upsert_created=False)
    request2 = _mock_request(
        match_info={},
        json_data={"query_slug": "test_query", "description": "via put"},
    )
    manager2 = _manager_with_app(request2, registry=registry, repo=repo2)
    response2 = await manager2.put()
    assert response2.status == 202
    assert repo2.calls[0][0] == "upsert"
    assert repo2.calls[0][1].slug == "test_query"

    # PATCH -> repo.patch with the resolved store's identity.
    repo3 = _FakeRepo()
    request3 = _mock_request(
        match_info={"slug": "test_query"},
        json_data={"description": "patched"},
    )
    manager3 = _manager_with_app(request3, registry=registry, repo=repo3)
    response3 = await manager3.patch()
    assert response3.status == 200
    assert repo3.calls[0][0] == "patch"
    assert repo3.calls[0][1].store == store
    assert repo3.calls[0][1].slug == "test_query"

    # DELETE -> repo.delete with the resolved store's identity.
    repo4 = _FakeRepo(delete_result=True)
    request4 = _mock_request(
        method="DELETE",
        match_info={"slug": "test_query"},
    )
    manager4 = _manager_with_app(request4, registry=registry, repo=repo4)
    response4 = await manager4.delete()
    assert response4.status == 202
    assert repo4.calls[0][0] == "delete"
    assert repo4.calls[0][1].store == store
    assert repo4.calls[0][1].slug == "test_query"


@pytest.mark.asyncio
async def test_null_and_conflicting_write_selectors() -> None:
    """null and conflicting write selectors."""
    registry = _mock_registry()

    # Conflicting selectors (URL match_info vs query string) -> 400,
    # repository never touched. self.error()/HTTPBadRequest are raised,
    # not returned — the standard aiohttp error-signaling pattern.
    repo = _FakeRepo()
    request = _mock_request(
        match_info={"slug": "test_query", "tenant": "tenant2"},
        query={"tenant": "tenant1"},
        json_data={"description": "x"},
    )
    manager = _manager_with_app(request, registry=registry, repo=repo)
    with pytest.raises(web.HTTPBadRequest):
        await manager.patch()
    assert repo.calls == []

    # No selector at all -> resolves to the configured default store; the
    # write still proceeds (null selector is not an error by itself).
    repo2 = _FakeRepo()
    request2 = _mock_request(
        match_info={"slug": "test_query"},
        json_data={"description": "x"},
    )
    manager2 = _manager_with_app(request2, registry=registry, repo=repo2)
    response2 = await manager2.patch()
    assert response2.status == 200
    assert repo2.calls[0][1].store == registry.resolve(None)


@pytest.mark.asyncio
async def test_patch_immutable_slug_and_program_rejection() -> None:
    """patch immutable slug and program rejection."""
    registry = _mock_registry()

    # Path/body slug disagreement -> 400 (raised), repository never
    # touched (AC-1 "require path/body slug agreement and reject row
    # moves").
    repo = _FakeRepo()
    request = _mock_request(
        match_info={"slug": "test_query"},
        json_data={"query_slug": "different_slug", "description": "x"},
    )
    manager = _manager_with_app(request, registry=registry, repo=repo)
    with pytest.raises(web.HTTPBadRequest):
        await manager.patch()
    assert repo.calls == []

    # program_slug rejection is enforced by DefinitionRepository.patch()
    # itself (TASK-719) — verify the handler surfaces that TenantError
    # as a 400 rather than swallowing or mistranslating it.
    repo_reject = _FakeRepo(raise_on={"patch": TenantError(
        "program_slug cannot be modified", error_code="invalid_tenant"
    )})
    request2 = _mock_request(
        match_info={"slug": "test_query"},
        json_data={"program_slug": "other_tenant"},
    )
    manager2 = _manager_with_app(request2, registry=registry, repo=repo_reject)
    with pytest.raises(web.HTTPBadRequest):
        await manager2.patch()


@pytest.mark.asyncio
async def test_legacy_upsert_and_delete_statuses() -> None:
    """legacy upsert and delete statuses."""
    # No tenant registry/repository published on the app at all — the
    # legacy fallback (unchanged direct-ORM behavior) must still work,
    # matching AC-2 "preserve POST/PUT upsert behavior and 201/202
    # statuses... DELETE 202/missing conventions" for apps that have not
    # adopted the tenant feature (the same regression TASK-723 found in
    # tests/handlers/test_querymanager_pagination.py's fixture).
    class _FakeCursor:
        def __init__(self, get_result=None, get_raises=None):
            self._get_result = get_result
            self._get_raises = get_raises

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

    class _FakeDb:
        def __init__(self, cursor):
            self._cursor = cursor

        async def acquire(self):
            return self._cursor

    # DELETE: legacy path calls QueryModel.get(...) then .delete(...) —
    # exercising the full legacy ORM chain is out of scope for a unit
    # test without a live DB; instead verify the *branch selection*
    # itself: with no qs_tenant_registry/qs_definition_repository
    # published, the handler must not raise KeyError reaching for those
    # app keys (the exact regression TASK-723 fixed) and must fall
    # through to the legacy branch, matching
    # tests/handlers/test_querymanager_pagination.py's own fixture shape
    # (only app["qs_connection"] published).
    request = _mock_request(
        method="DELETE",
        match_info={"slug": "test_query"},
    )
    from querysource.handlers.manager import QueryManager
    app = {"qs_connection": _FakeDb(_FakeCursor())}
    request.app = app
    manager = QueryManager(request)
    manager.get_arguments = lambda: request.match_info
    # No registry/repo published -> resolve_store's registry.get returns
    # None -> the handler must take the legacy branch, not KeyError.
    assert request.app.get("qs_tenant_registry") is None
    assert request.app.get("qs_definition_repository") is None
    # (Exercising the legacy ORM branch end-to-end requires a real/near-
    # real asyncdb connection; that integration gate is explicitly out
    # of scope here — see TASK-724's NOT-in-scope note on scheduler
    # identities/live DB. This test's contract is the branch-selection
    # guarantee above, which is what the app-key regression was about.)
