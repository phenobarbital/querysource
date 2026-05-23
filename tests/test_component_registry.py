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

    def test_all_have_non_empty_usage(self):
        """Usage falls back to the docstring body; no component should be blank."""
        ComponentRegistry.discover_all.cache_clear()
        catalog = ComponentRegistry.get_catalog()
        blanks = [c.name for c in catalog if not c.usage]
        assert not blanks, f"Components with empty usage: {blanks}"

    def test_all_have_icon(self):
        """Every component must have an icon (from _icon attr or category default)."""
        ComponentRegistry.discover_all.cache_clear()
        catalog = ComponentRegistry.get_catalog()
        blanks = [c.name for c in catalog if not c.icon]
        assert not blanks, f"Components with empty icon: {blanks}"

    def test_example_is_string(self):
        """The example field is rendered as a JSON/YAML text snippet."""
        ComponentRegistry.discover_all.cache_clear()
        catalog = ComponentRegistry.get_catalog()
        for c in catalog:
            assert isinstance(c.example, str), (
                f"{c.name}.example must be a string, got {type(c.example).__name__}"
            )

    def test_join_example_is_balanced_json(self):
        """Regression: brace-balanced parsing keeps the outer closing brace."""
        ComponentRegistry.discover_all.cache_clear()
        catalog = {c.name: c for c in ComponentRegistry.get_catalog()}
        ex = catalog["Join"].example
        assert ex, "Join must have an example"
        assert ex.count("{") == ex.count("}"), (
            f"Unbalanced braces in Join example:\n{ex}"
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
        # TableDestination publishes a ``_catalog`` override renaming it to
        # "Table" (the YAML step-name); all the others are introspected.
        for name in ("ToSharepoint", "ToS3", "Table", "DWHDestination"):
            ci = catalog[name]
            assert ci.category == "Destinations"
            assert ci.json_schema.get("properties"), (
                f"Expected populated JSON schema for {name}; got {ci.json_schema}"
            )

    def test_catalog_renames_table_output_adapter_to_table_output(self):
        """TableOutputAdapter publishes a ``_catalog`` override naming it ``TableOutput``.

        The Python class is ``TableOutputAdapter`` for historical reasons, but
        the YAML step-name and the UI display name are both ``TableOutput``.
        The override also populates attributes, json_schema, and example.
        """
        from querysource.queries.multi.registry import ComponentRegistry
        ComponentRegistry.discover_all.cache_clear()
        catalog = {ci.name: ci for ci in ComponentRegistry.get_catalog()}
        entry = catalog.get("TableOutput")
        assert entry is not None, (
            f"Expected 'TableOutput' in catalog; found keys: {list(catalog.keys())}"
        )
        assert entry.category == "Destinations"
        # The override should populate the catalog fully.
        attr_names = {a.name for a in entry.attributes}
        assert {"flavor", "tablename", "if_exists", "pk"}.issubset(attr_names)
        assert entry.json_schema and entry.json_schema.get("properties")
        assert entry.example, "Expected a non-empty example for TableOutput"
        # The old display name must no longer be present.
        assert "TableOutputAdapter" not in catalog


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
        assert info.example == ""
        assert info.icon == ""

    def test_validation_error_creation(self):
        err = ValidationError(step="Join", field="left", message="Missing left key")
        assert err.step == "Join"
        assert err.field == "left"
        assert err.message == "Missing left key"

    def test_validation_result_defaults(self):
        result = ValidationResult(valid=True)
        assert result.valid is True
        assert result.errors == []
