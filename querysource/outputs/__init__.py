"""Supported Outputs for QuerySource.

``DataOutput`` is resolved lazily (PEP 562) so importing a subpackage such as
``querysource.outputs.dt`` does not load ``output.py`` and its writer
registry (FEAT-154).
"""

__all__ = ('DataOutput', )


def __getattr__(name: str) -> type:
    """Import ``DataOutput`` from ``.output`` on first access.

    Args:
        name: attribute requested on the package.

    Returns:
        type: the ``DataOutput`` class.

    Raises:
        AttributeError: for any name other than ``DataOutput``.
    """
    if name == "DataOutput":
        from .output import DataOutput

        globals()["DataOutput"] = DataOutput
        return DataOutput
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Expose ``DataOutput`` to ``dir()``."""
    return sorted(__all__)
