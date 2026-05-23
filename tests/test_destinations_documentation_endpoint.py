"""FEAT-097 acceptance — destinations must expose populated JSON schemas
through the FEAT-095 documentation endpoint (ComponentRegistry.get_catalog()).
"""


def test_every_real_destination_has_populated_schema():
    from querysource.queries.multi.registry import ComponentRegistry
    ComponentRegistry.discover_all.cache_clear()

    catalog = ComponentRegistry.get_catalog()
    destinations = {ci.name: ci for ci in catalog if ci.category == "Destinations"}

    # TableOutputAdapter wraps the legacy TableOutput which is not introspectable —
    # empty schema is explicitly allowed per spec §5.
    adapter_exempt_names = {"TableOutputAdapter"}

    failures = []
    for name, ci in destinations.items():
        if name in adapter_exempt_names:
            continue
        props = ci.json_schema.get("properties") or {}
        if not props:
            failures.append(name)
    assert not failures, (
        f"Destinations with empty json_schema.properties: {failures}. "
        "Every destination except TableOutputAdapter must produce a populated schema."
    )


def test_destinations_category_in_catalog():
    from querysource.queries.multi.registry import ComponentRegistry
    ComponentRegistry.discover_all.cache_clear()
    categories = {ci.category for ci in ComponentRegistry.get_catalog()}
    assert "Destinations" in categories


def test_all_four_migrated_destinations_present_in_catalog():
    from querysource.queries.multi.registry import ComponentRegistry
    ComponentRegistry.discover_all.cache_clear()
    catalog_names = {ci.name for ci in ComponentRegistry.get_catalog()}
    for name in ("ToSharepoint", "ToS3", "TableDestination", "DWHDestination"):
        assert name in catalog_names, f"Expected {name} in catalog; found: {sorted(catalog_names)}"


def test_table_output_adapter_present_and_destinations_category():
    from querysource.queries.multi.registry import ComponentRegistry
    ComponentRegistry.discover_all.cache_clear()
    catalog = {ci.name: ci for ci in ComponentRegistry.get_catalog()}
    assert "TableOutputAdapter" in catalog
    assert catalog["TableOutputAdapter"].category == "Destinations"


def test_backward_compat_imports():
    """Smoke-test that all public paths from outputs/destinations are importable
    by verifying direct attribute access on already-loaded modules."""
    import querysource.outputs.destinations as od
    import querysource.outputs.destinations.abstract as abstract_mod
    import querysource.queries.multi.destinations as multi_dest

    # Verify key symbols are accessible
    assert hasattr(od, "DESTINATION_REGISTRY")
    assert hasattr(od, "get_destination")
    assert hasattr(od, "AbstractDestination")
    assert hasattr(abstract_mod, "AbstractDestination")
    assert hasattr(multi_dest, "DESTINATION_REGISTRY")
    assert hasattr(multi_dest, "AbstractDestination")
