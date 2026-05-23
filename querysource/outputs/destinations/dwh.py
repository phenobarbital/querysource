"""Backward-compatibility shim.

The real class now lives at querysource.queries.multi.destinations.dwh.
This file is retained so that existing imports of the form::

    from querysource.outputs.destinations.dwh import DWHDestination

continue to work without modification.
"""
from querysource.queries.multi.destinations.dwh import (  # noqa: F401
    DWHDestination,
    _clean_dynamo_record,
)

__all__ = ("DWHDestination",)
