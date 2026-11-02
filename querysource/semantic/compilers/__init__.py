"""Semantic compilers (FEAT-175). Importing this package does not import sidemantic."""
from __future__ import annotations

from ..protocol import SemanticCompiler


def get_compiler(name: str = "sidemantic") -> SemanticCompiler:
    """Return the compiler implementation.

    Args:
        name: Compiler identifier; only ``"sidemantic"`` is supported.

    Returns:
        A ready :class:`SemanticCompiler`.

    Raises:
        SemanticResourceError: If ``name`` is not a known compiler.
        SemanticCompilerUnavailable: If the optional compiler is not installed.
    """
    from ..exceptions import SemanticResourceError

    if name != "sidemantic":
        raise SemanticResourceError(f"Unknown semantic compiler: {name}")
    from ..loader import get_loader
    from .sidemantic import SidemanticCompiler

    return SidemanticCompiler(get_loader())
