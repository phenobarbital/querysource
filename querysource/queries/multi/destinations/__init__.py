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

_pkg_logger = _logging.getLogger(__name__)


def _scan_destinations() -> dict:
    """Scan this folder for AbstractDestination subclasses and return a registry.

    The import of AbstractDestination is deferred to inside this function to
    avoid a circular import: outputs/destinations/__init__.py imports
    queries/multi/destinations (this package), and this package's module-level
    import of outputs.destinations.abstract would trigger outputs.destinations.__init__
    which in turn would re-enter this package before initialization completes.
    """
    # Deferred import to break the circular: outputs.destinations -> abstract ->
    # (loads outputs.destinations.__init__) -> queries.multi.destinations -> here
    from querysource.outputs.destinations.abstract import AbstractDestination  # noqa: PLC0415

    registry: dict = {}
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
            _pkg_logger.warning(
                "Skipping '%s': optional dependency missing (%s). "
                "Install the required package to enable this destination.",
                stem, exc,
            )
            continue
        for cls_name, obj in inspect.getmembers(module, inspect.isclass):
            if obj is AbstractDestination:
                continue
            if issubclass(obj, AbstractDestination) and obj.__module__ == module.__name__:
                registry[cls_name] = obj
    return registry


# Expose AbstractDestination as a convenience re-export.
# The import is deferred here too; callers that need the class should import
# it directly from querysource.outputs.destinations.abstract.
def _get_abstract_destination():
    from querysource.outputs.destinations.abstract import AbstractDestination
    return AbstractDestination


DESTINATION_REGISTRY: dict = _scan_destinations()


def __getattr__(name: str):
    """Lazy attribute access for AbstractDestination to avoid circular import."""
    if name == "AbstractDestination":
        return _get_abstract_destination()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    """Include lazily-resolved names so dir() and hasattr() stay consistent."""
    return __all__


__all__ = ("AbstractDestination", "DESTINATION_REGISTRY")
