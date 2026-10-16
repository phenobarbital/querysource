"""Compiler adapter contract (FEAT-175 M1)."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import CatalogField, CompilationTarget, CompiledQuery, SemanticModelRef, SemanticQuery


@runtime_checkable
class SemanticCompiler(Protocol):
    """Implementations never leak their internal objects through CompiledQuery."""

    def compile(
        self, model: SemanticModelRef, request: SemanticQuery, target: CompilationTarget
    ) -> CompiledQuery:
        """Compile ``request`` to literal SQL for ``target.dialect``."""

    def catalog(self, model: SemanticModelRef, target: CompilationTarget) -> list[CatalogField]:
        """List metrics and dimensions of the model."""
