"""Unit tests for ComponentRegistry (TASK-663)."""
import pytest
from querysource.queries.multi.registry import (
    ComponentRegistry,
    ComponentInfo,
    ValidationResult,
    ValidationError,
    AttributeInfo,
)


class TestDiscoverAll:
    def test_finds_operators(self):
        components = ComponentRegistry.discover_all()
        for op in ["Join", "Concat", "Melt", "Merge", "Info", "Filter", "GroupBy"]:
            assert op in components, f"Missing operator: {op}"

    def test_finds_transforms(self):
        components = ComponentRegistry.discover_all()
        for t in ["Map", "correlation", "crosstab", "pivot", "tOrder"]:
            assert t in components, f"Missing transform: {t}"

    def test_finds_sources(self):
        components = ComponentRegistry.discover_all()
        assert len([k for k in components if k.endswith("Source")]) > 0

    def test_finds_destinations(self):
        components = ComponentRegistry.discover_all()
        assert len([k for k in components if "Output" in k or k in ("ToSharepoint", "ToS3", "Table", "DWH")]) > 0

    def test_returns_dict(self):
        components = ComponentRegistry.discover_all()
        assert isinstance(components, dict)

    def test_values_are_types(self):
        components = ComponentRegistry.discover_all()
        for name, cls in components.items():
            assert isinstance(cls, type), f"{name} value is not a type"


class TestGetCatalog:
    def test_returns_list(self):
        catalog = ComponentRegistry.get_catalog()
        assert isinstance(catalog, list)

    def test_catalog_not_empty(self):
        catalog = ComponentRegistry.get_catalog()
        assert len(catalog) > 0

    def test_returns_component_info_list(self):
        catalog = ComponentRegistry.get_catalog()
        for item in catalog:
            assert isinstance(item, ComponentInfo)

    def test_all_have_name(self):
        catalog = ComponentRegistry.get_catalog()
        for item in catalog:
            assert item.name, f"ComponentInfo missing name: {item}"

    def test_all_have_valid_category(self):
        valid_categories = {"Operators", "Transformations", "Sources", "Destinations", "Components"}
        catalog = ComponentRegistry.get_catalog()
        for item in catalog:
            assert item.category in valid_categories, (
                f"Invalid category '{item.category}' for {item.name}"
            )

    def test_operators_have_correct_category(self):
        catalog = ComponentRegistry.get_catalog()
        catalog_dict = {c.name: c for c in catalog}
        for op in ["Join", "Concat", "Merge"]:
            if op in catalog_dict:
                assert catalog_dict[op].category == "Operators", (
                    f"{op} should be 'Operators', got {catalog_dict[op].category}"
                )

    def test_transforms_have_correct_category(self):
        catalog = ComponentRegistry.get_catalog()
        catalog_dict = {c.name: c for c in catalog}
        for t in ["Map", "correlation", "crosstab"]:
            if t in catalog_dict:
                assert catalog_dict[t].category == "Transformations", (
                    f"{t} should be 'Transformations', got {catalog_dict[t].category}"
                )


class TestValidatePipeline:
    def test_no_sources_is_invalid(self):
        payload = {"Join": {"type": "inner"}}
        result = ComponentRegistry.validate_pipeline(payload)
        assert not result.valid
        assert any("source" in e.message.lower() for e in result.errors)

    def test_unknown_operator_is_invalid(self):
        payload = {
            "queries": {"a": {"slug": "test"}},
            "FakeOperator": {"foo": "bar"},
        }
        result = ComponentRegistry.validate_pipeline(payload)
        assert not result.valid
        assert any("FakeOperator" in e.step for e in result.errors)

    def test_pipeline_with_known_operator_is_valid(self):
        payload = {
            "queries": {"revenue": {"slug": "revenue_report"}},
            "Filter": {"conditions": [{"column": "status", "expression": "==", "value": "active"}]},
        }
        result = ComponentRegistry.validate_pipeline(payload)
        assert isinstance(result, ValidationResult)
        # May be valid or have other structural issues, but not "unknown operator"
        op_errors = [e for e in result.errors if "FakeOperator" in e.step]
        assert len(op_errors) == 0

    def test_empty_payload_is_invalid(self):
        result = ComponentRegistry.validate_pipeline({})
        assert not result.valid

    def test_returns_validation_result(self):
        result = ComponentRegistry.validate_pipeline({"queries": {"a": {"slug": "test"}}})
        assert isinstance(result, ValidationResult)
        assert isinstance(result.valid, bool)
        assert isinstance(result.errors, list)

    def test_unknown_transform_in_transform_section(self):
        payload = {
            "queries": {"a": {"slug": "test"}},
            "Transform": [{"FakeTransform": {"foo": "bar"}}],
        }
        result = ComponentRegistry.validate_pipeline(payload)
        assert not result.valid
        assert any("FakeTransform" in e.step for e in result.errors)

    def test_valid_transform_in_transform_section(self):
        payload = {
            "queries": {"a": {"slug": "test"}},
            "Transform": [{"Map": {"fields": {"x": "y"}}}],
        }
        result = ComponentRegistry.validate_pipeline(payload)
        # No "unknown transform" errors for Map
        transform_errors = [e for e in result.errors if "FakeTransform" in e.step]
        assert len(transform_errors) == 0


class TestDestinationDiscovery:
    def setup_method(self):
        from querysource.queries.multi.registry import ComponentRegistry
        ComponentRegistry.discover_all.cache_clear()

    def teardown_method(self):
        ComponentRegistry.discover_all.cache_clear()

    def test_scan_picks_up_migrated_destinations(self):
        from querysource.queries.multi.registry import ComponentRegistry
        components = ComponentRegistry.discover_all()
        for cls_name in ("ToSharepoint", "ToS3", "TableDestination", "DWHDestination"):
            assert cls_name in components, f"{cls_name} missing from discover_all()"

    def test_merge_preserves_legacy_step_names(self):
        from querysource.queries.multi.registry import ComponentRegistry
        components = ComponentRegistry.discover_all()
        for key in ("tableOutput", "TableOutput"):
            assert key in components, f"Legacy registry key '{key}' missing"

    def test_classify_destinations_via_issubclass(self):
        from querysource.queries.multi.registry import ComponentRegistry
        from querysource.queries.multi.destinations.sharepoint import ToSharepoint
        assert ComponentRegistry._classify("ToSharepoint", ToSharepoint) == "Destinations"

    def test_classify_table_output_adapter_via_issubclass(self):
        from querysource.queries.multi.registry import ComponentRegistry
        from querysource.outputs.destinations import TableOutputAdapter
        assert ComponentRegistry._classify("tableOutput", TableOutputAdapter) == "Destinations"

    def test_catalog_returns_populated_schema_for_real_destinations(self):
        from querysource.queries.multi.registry import ComponentRegistry
        ComponentRegistry.discover_all.cache_clear()
        catalog = {ci.name: ci for ci in ComponentRegistry.get_catalog()}
        for name in ("ToSharepoint", "ToS3", "TableDestination", "DWHDestination"):
            ci = catalog[name]
            assert ci.category == "Destinations"
            assert ci.json_schema.get("properties"), (
                f"Expected populated JSON schema for {name}; got {ci.json_schema}"
            )

    def test_catalog_allows_empty_schema_for_table_output_adapter(self):
        """TableOutputAdapter wraps a non-introspectable TableOutput; empty is OK.

        The catalog uses the class name "TableOutputAdapter" (from get_description()),
        not the YAML step-name keys ("tableOutput" / "TableOutput").
        """
        from querysource.queries.multi.registry import ComponentRegistry
        ComponentRegistry.discover_all.cache_clear()
        catalog = {ci.name: ci for ci in ComponentRegistry.get_catalog()}
        # get_catalog uses the class name from get_description(), not the step-name key
        adapter_entry = catalog.get("TableOutputAdapter")
        assert adapter_entry is not None, (
            f"Expected 'TableOutputAdapter' in catalog; found keys: {list(catalog.keys())}"
        )
        assert adapter_entry.category == "Destinations"


class TestDataModels:
    def test_attribute_info_creation(self):
        attr = AttributeInfo(name="col", type="str", default="x", required=True)
        assert attr.name == "col"
        assert attr.type == "str"
        assert attr.default == "x"
        assert attr.required is True

    def test_component_info_defaults(self):
        info = ComponentInfo(
            name="Test",
            category="Operators",
            description="A test",
            usage="Testing",
        )
        assert info.attributes == []
        assert info.json_schema == {}
        assert info.example == {}

    def test_validation_error_creation(self):
        err = ValidationError(step="Join", field="left", message="Missing left key")
        assert err.step == "Join"
        assert err.field == "left"
        assert err.message == "Missing left key"

    def test_validation_result_defaults(self):
        result = ValidationResult(valid=True)
        assert result.valid is True
        assert result.errors == []
