"""Output writers for QuerySource, resolved lazily on first access (FEAT-154).

Importing this package (or any submodule such as ``.abstract``) no longer
imports every writer. ``from querysource.outputs.writers import PDFWriter``
imports only ``.pdf`` (PEP 562 ``__getattr__``).
"""

from importlib import import_module

_WRITER_MODULES: dict[str, str] = {
    "jsonWriter": ".json",
    "TXTWriter": ".txt",
    "CSVWriter": ".csv",
    "ExcelWriter": ".excel",
    "HTMLWriter": ".html",
    "BokehWriter": ".bokeh",
    "PlotlyWriter": ".plotly",
    "TSVWriter": ".tsv",
    "ReportWriter": ".report",
    "PickleWriter": ".pickle",
    "TableWriter": ".table",
    "PDFWriter": ".pdf",
    # "ProfileWriter": ".profiling",
    # "EDAWriter": ".eda",
    # "DescribeWriter": ".describe",
    # "ClusterWriter": ".clustering",
    "XMLWriter": ".xml",
}

__all__ = tuple(_WRITER_MODULES)


def __getattr__(name: str) -> type:
    """Import and cache the writer class ``name`` on first access (PEP 562).

    Args:
        name: a writer class name listed in ``__all__``.

    Returns:
        type: the writer class.

    Raises:
        AttributeError: if ``name`` is not a registered writer.
    """
    # Look up `name` in `_WRITER_MODULES`; if absent raise
    # AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # (exact message — bounded by spec §7 Patterns, destinations/__init__.py:80).
    # Otherwise `cls = getattr(import_module(_WRITER_MODULES[name], __name__), name)`,
    # store `globals()[name] = cls`, return cls — bounded by AC5 + identity (spec §7).
    if name not in _WRITER_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    cls = getattr(import_module(_WRITER_MODULES[name], __name__), name)
    globals()[name] = cls
    return cls


def __dir__() -> list[str]:
    """Expose the lazily-resolved writer names to ``dir()``."""
    return sorted(__all__)
