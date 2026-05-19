from .base import ThreadSource
from .file import ThreadFile
from .query import ThreadQuery
from .s3 import SourceS3
from .sharepoint import SourceSharepoint
from .smartsheet import SourceSmartSheet
from .table import SourceTable

__all__ = [
    "ThreadSource",
    "ThreadQuery",
    "ThreadFile",
    "SourceSharepoint",
    "SourceSmartSheet",
    "SourceS3",
    "SourceTable",
    "SOURCE_REGISTRY",
]

#: Registry mapping source type names (as used in YAML config) to their classes.
#: Used by :class:`~querysource.queries.multi.MultiQS` for dynamic dispatch.
SOURCE_REGISTRY: dict = {
    "SourceSharepoint": SourceSharepoint,
    "SourceSmartSheet": SourceSmartSheet,
    "SourceS3": SourceS3,
    "SourceTable": SourceTable,
}
