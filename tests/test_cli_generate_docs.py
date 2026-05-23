"""Tests for CLI documentation generator (TASK-664)."""
import json
import pytest
from pathlib import Path
from querysource.cli.generate_docs import generate_docs, _dataclass_to_dict


class TestGenerateDocs:
    def test_generates_files(self, tmp_path):
        count = generate_docs(output_dir=str(tmp_path))
        json_files = list(tmp_path.glob("*.json"))
        assert len(json_files) > 0
        assert count > 0

    def test_file_has_required_fields(self, tmp_path):
        generate_docs(output_dir=str(tmp_path))
        json_files = list(tmp_path.glob("*.json"))
        assert len(json_files) > 0
        with open(json_files[0]) as f:
            data = json.load(f)
        for field in ["name", "category", "description", "json_schema", "attributes"]:
            assert field in data, f"Missing field: {field}"

    def test_json_schema_format(self, tmp_path):
        generate_docs(output_dir=str(tmp_path))
        json_files = list(tmp_path.glob("*.json"))
        assert len(json_files) > 0
        with open(json_files[0]) as f:
            data = json.load(f)
        assert "$schema" in data["json_schema"]

    def test_category_filter(self, tmp_path):
        generate_docs(output_dir=str(tmp_path), category="Operators")
        json_files = list(tmp_path.glob("*.json"))
        assert len(json_files) > 0
        for f in json_files:
            with open(f) as fh:
                data = json.load(fh)
            assert data["category"] == "Operators", (
                f"{f.name} has category {data['category']}, expected Operators"
            )

    def test_returns_count(self, tmp_path):
        count = generate_docs(output_dir=str(tmp_path))
        json_files = list(tmp_path.glob("*.json"))
        assert count == len(json_files)

    def test_creates_output_dir(self, tmp_path):
        subdir = tmp_path / "nested" / "docs"
        generate_docs(output_dir=str(subdir))
        assert subdir.exists()

    def test_summary_format(self, tmp_path):
        generate_docs(output_dir=str(tmp_path), category="Operators", fmt="summary")
        txt_files = list(tmp_path.glob("*.txt"))
        assert len(txt_files) > 0
        content = txt_files[0].read_text()
        assert "Component:" in content
        assert "Category: Operators" in content


class TestDataclassToDict:
    def test_converts_nested_dataclass(self):
        from querysource.queries.multi.registry import ComponentInfo, AttributeInfo
        info = ComponentInfo(
            name="Test",
            category="Operators",
            description="desc",
            usage="use",
            attributes=[AttributeInfo(name="x", type="str", default="a", required=True)],
        )
        result = _dataclass_to_dict(info)
        assert isinstance(result, dict)
        assert result["name"] == "Test"
        assert isinstance(result["attributes"], list)
        assert result["attributes"][0]["name"] == "x"

    def test_converts_list(self):
        result = _dataclass_to_dict([1, 2, 3])
        assert result == [1, 2, 3]

    def test_passes_through_scalars(self):
        assert _dataclass_to_dict("hello") == "hello"
        assert _dataclass_to_dict(42) == 42
        assert _dataclass_to_dict(None) is None
