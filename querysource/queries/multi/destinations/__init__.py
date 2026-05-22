"""
querysource.queries.multi.destinations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Canonical home for MultiQuery-only destination components.

This package re-exports :class:`AbstractDestination` (defined in
:mod:`querysource.outputs.destinations.abstract` for Flowtask compatibility)
and exposes a folder-scanned :data:`DESTINATION_REGISTRY` of every destination
class living under this package.

The legacy registry at :mod:`querysource.outputs.destinations` continues to
host :class:`TableOutputAdapter` (Flowtask-shared) and aggregates entries from
this package via backward-compat shims.
"""
from __future__ import annotations

import importlib
import inspect
import logging as _logging
from pathlib import Path

from querysource.outputs.destinations.abstract import AbstractDestination

_pkg_logger = _logging.getLogger(__name__)


def _scan_destinations() -> dict[str, type[AbstractDestination]]:
    """Scan this folder for AbstractDestination subclasses and return a registry."""
    registry: dict[str, type[AbstractDestination]] = {}
    pkg_dir = Path(__file__).parent
    for py_file in sorted(pkg_dir.glob("*.py")):
        name = py_file.name
        if name.startswith("_") or name == "abstract.py":
            continue
        stem = py_file.stem
        try:
            module = importlib.import_module(
                f".{stem}", package="querysource.queries.multi.destinations"
            )
        except ImportError as exc:
            _pkg_logger.debug(
                "Destination module '%s' skipped (optional dep missing): %s",
                stem, exc,
            )
            continue
        for cls_name, obj in inspect.getmembers(module, inspect.isclass):
            if obj is AbstractDestination:
                continue
            if issubclass(obj, AbstractDestination) and obj.__module__ == module.__name__:
                registry[cls_name] = obj
    return registry


DESTINATION_REGISTRY: dict[str, type[AbstractDestination]] = _scan_destinations()


__all__ = ("AbstractDestination", "DESTINATION_REGISTRY")
