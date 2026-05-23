"""Backward-compatibility shim.

The real class now lives at querysource.queries.multi.destinations.s3.
This file is retained so that existing imports of the form::

    from querysource.outputs.destinations.s3 import ToS3

continue to work without modification.
"""
from querysource.queries.multi.destinations.s3 import ToS3  # noqa: F401

__all__ = ("ToS3",)
