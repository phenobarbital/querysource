"""Backward-compatibility shim.

The real class now lives at querysource.queries.multi.destinations.table.
This file is retained so that existing imports of the form::

    from querysource.outputs.destinations.table import TableDestination

continue to work without modification.
"""
from querysource.queries.multi.destinations.table import (  # noqa: F401
    TableDestination,
    DRIVER_MAP,
    VALID_METHODS,
    _EXTERNAL_DRIVERS,
)

__all__ = ("TableDestination", "DRIVER_MAP", "VALID_METHODS", "_EXTERNAL_DRIVERS")
