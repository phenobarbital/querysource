"""Tests for AirtableSource registration in SOURCE_REGISTRY (TASK-677)."""
from querysource.queries.multi.sources import (
    SOURCE_REGISTRY,
    AirtableSource,
    OneDriveSource,
    S3Source,
    SharepointSource,
    SmartSheetSource,
    TableSource,
)


class TestSourceRegistry:
    def test_airtable_registered(self):
        assert "AirtableSource" in SOURCE_REGISTRY
        assert SOURCE_REGISTRY["AirtableSource"] is AirtableSource

    def test_existing_sources_still_registered(self):
        # Regression: previously registered sources must still be present.
        assert SOURCE_REGISTRY["SharepointSource"] is SharepointSource
        assert SOURCE_REGISTRY["SmartSheetSource"] is SmartSheetSource
        assert SOURCE_REGISTRY["S3Source"] is S3Source
        assert SOURCE_REGISTRY["TableSource"] is TableSource

    def test_airtable_in_all(self):
        import querysource.queries.multi.sources as mod
        assert "AirtableSource" in mod.__all__

    def test_onedrive_registered(self):
        import querysource.queries.multi.sources as mod
        assert SOURCE_REGISTRY["OneDriveSource"] is OneDriveSource
        assert "OneDriveSource" in mod.__all__
