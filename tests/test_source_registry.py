"""Unit tests for source registry and __init__ exports (TASK-652)."""
import pytest

from querysource.queries.multi.sources import (
    SOURCE_REGISTRY,
    ThreadFile,
    ThreadQuery,
    ThreadSource,
    SourceS3,
    SourceSharepoint,
    SourceSmartSheet,
    SourceTable,
    __all__,
)


class TestSourceRegistry:
    def test_registry_contains_sharepoint(self):
        assert "SourceSharepoint" in SOURCE_REGISTRY

    def test_registry_contains_smartsheet(self):
        assert "SourceSmartSheet" in SOURCE_REGISTRY

    def test_registry_contains_s3(self):
        assert "SourceS3" in SOURCE_REGISTRY

    def test_registry_contains_table(self):
        assert "SourceTable" in SOURCE_REGISTRY

    def test_registry_has_exactly_four_sources(self):
        assert len(SOURCE_REGISTRY) == 4

    def test_registry_values_are_thread_source_subclasses(self):
        for name, cls in SOURCE_REGISTRY.items():
            assert issubclass(cls, ThreadSource), (
                f"{name} is not a ThreadSource subclass"
            )

    def test_registry_does_not_contain_thread_query(self):
        assert "ThreadQuery" not in SOURCE_REGISTRY

    def test_registry_does_not_contain_thread_file(self):
        assert "ThreadFile" not in SOURCE_REGISTRY

    def test_registry_classes_match_imported_classes(self):
        assert SOURCE_REGISTRY["SourceSharepoint"] is SourceSharepoint
        assert SOURCE_REGISTRY["SourceSmartSheet"] is SourceSmartSheet
        assert SOURCE_REGISTRY["SourceS3"] is SourceS3
        assert SOURCE_REGISTRY["SourceTable"] is SourceTable

    def test_all_contains_thread_source(self):
        assert "ThreadSource" in __all__

    def test_all_contains_thread_query(self):
        assert "ThreadQuery" in __all__

    def test_all_contains_thread_file(self):
        assert "ThreadFile" in __all__

    def test_all_contains_source_registry(self):
        assert "SOURCE_REGISTRY" in __all__

    def test_all_contains_all_new_sources(self):
        assert "SourceSharepoint" in __all__
        assert "SourceSmartSheet" in __all__
        assert "SourceS3" in __all__
        assert "SourceTable" in __all__

    def test_thread_source_importable(self):
        assert ThreadSource is not None

    def test_thread_query_still_importable(self):
        assert ThreadQuery is not None

    def test_thread_file_still_importable(self):
        assert ThreadFile is not None
