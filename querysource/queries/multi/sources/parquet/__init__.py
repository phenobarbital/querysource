"""Parquet sources for MultiQS (FEAT-158): local filesystem, S3 and GCS over fsspec."""

from .base import ParquetSource
from .gcs import ParquetGCSSource
from .local import ParquetFileSource
from .s3 import ParquetS3Source

__all__ = ["ParquetSource", "ParquetFileSource", "ParquetS3Source", "ParquetGCSSource"]
