"""Unit tests for source registry and __init__ exports (TASK-652)."""
import pytest

from querysource.queries.multi.sources import (
    SOURCE_REGISTRY,
    AirtableSource,
    FileSource,
    ThreadQuery,
    ThreadSource,
    S3Source,
    SharepointSource,
    SmartSheetSource,
    TableSource,
    __all__,
)


class TestSourceRegistry:
    def test_registry_contains_airtable(self):
        assert "AirtableSource" in SOURCE_REGISTRY

    def test_registry_contains_sharepoint(self):
        assert "SharepointSource" in SOURCE_REGISTRY

    def test_registry_contains_smartsheet(self):
        assert "SmartSheetSource" in SOURCE_REGISTRY

    def test_registry_contains_s3(self):
        assert "S3Source" in SOURCE_REGISTRY

    def test_registry_contains_table(self):
        assert "TableSource" in SOURCE_REGISTRY

    def test_registry_has_exactly_five_sources(self):
        assert len(SOURCE_REGISTRY) == 5

    def test_registry_values_are_thread_source_subclasses(self):
        for name, cls in SOURCE_REGISTRY.items():
            assert issubclass(cls, ThreadSource), (
                f"{name} is not a ThreadSource subclass"
            )

    def test_registry_does_not_contain_thread_query(self):
        assert "ThreadQuery" not in SOURCE_REGISTRY

    def test_registry_does_not_contain_file_source(self):
        assert "FileSource" not in SOURCE_REGISTRY

    def test_registry_classes_match_imported_classes(self):
        assert SOURCE_REGISTRY["AirtableSource"] is AirtableSource
        assert SOURCE_REGISTRY["SharepointSource"] is SharepointSource
        assert SOURCE_REGISTRY["SmartSheetSource"] is SmartSheetSource
        assert SOURCE_REGISTRY["S3Source"] is S3Source
        assert SOURCE_REGISTRY["TableSource"] is TableSource

    def test_all_contains_thread_source(self):
        assert "ThreadSource" in __all__

    def test_all_contains_thread_query(self):
        assert "ThreadQuery" in __all__

    def test_all_contains_file_source(self):
        assert "FileSource" in __all__

    def test_all_contains_source_registry(self):
        assert "SOURCE_REGISTRY" in __all__

    def test_all_contains_airtable_source(self):
        assert "AirtableSource" in __all__

    def test_all_contains_all_new_sources(self):
        assert "SharepointSource" in __all__
        assert "SmartSheetSource" in __all__
        assert "S3Source" in __all__
        assert "TableSource" in __all__

    def test_thread_source_importable(self):
        assert ThreadSource is not None

    def test_thread_query_still_importable(self):
        assert ThreadQuery is not None

    def test_file_source_importable(self):
        assert FileSource is not None

    def test_airtable_source_importable(self):
        assert AirtableSource is not None
