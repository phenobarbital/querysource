"""Backward-compatibility shim.

The real class now lives at querysource.queries.multi.destinations.sharepoint.
This file is retained so that existing imports of the form::

    from querysource.outputs.destinations.sharepoint import ToSharepoint

continue to work without modification.
"""
from querysource.queries.multi.destinations.sharepoint import ToSharepoint  # noqa: F401

__all__ = ("ToSharepoint",)
