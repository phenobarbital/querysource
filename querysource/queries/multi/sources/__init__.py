from .airtable import AirtableSource
from .base import ThreadSource
from .executors import LocalExecutor, QueryExecutor, RemoteConfig, RemoteExecutor
from .file import FileSource
from .onedrive import OneDriveSource
from .query import ThreadQuery
from .s3 import S3Source
from .sharepoint import SharepointSource
from .smartsheet import SmartSheetSource
from .table import TableSource

__all__ = [
    "ThreadSource",
    "ThreadQuery",
    "FileSource",
    "AirtableSource",
    "OneDriveSource",
    "SharepointSource",
    "SmartSheetSource",
    "S3Source",
    "TableSource",
    "SOURCE_REGISTRY",
    "QueryExecutor",
    "LocalExecutor",
    "RemoteExecutor",
    "RemoteConfig",
]

#: Registry mapping source type names (as used in YAML config) to their classes.
#: Used by :class:`~querysource.queries.multi.MultiQS` for dynamic dispatch.
SOURCE_REGISTRY: dict = {
    "AirtableSource": AirtableSource,
    "OneDriveSource": OneDriveSource,
    "SharepointSource": SharepointSource,
    "SmartSheetSource": SmartSheetSource,
    "S3Source": S3Source,
    "TableSource": TableSource,
}
