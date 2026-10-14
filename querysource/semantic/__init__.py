"""QuerySource semantic layer (FEAT-175). Importing this package never imports sidemantic."""
from .exceptions import (
    SemanticCompilerUnavailable,
    SemanticError,
    SemanticFieldDenied,
    SemanticGovernanceError,
    SemanticRequestError,
    SemanticResourceError,
)
from .models import (
    FILTER_OPS,
    TIME_GRAINS,
    CatalogField,
    CatalogPayload,
    CompilationTarget,
    CompiledQuery,
    SemanticFilter,
    SemanticModelRef,
    SemanticQuery,
    SemanticResource,
)
from .protocol import SemanticCompiler
from .api import compile, execute  # noqa: A004

__all__ = (
    "FILTER_OPS",
    "TIME_GRAINS",
    "CatalogField",
    "CatalogPayload",
    "CompilationTarget",
    "CompiledQuery",
    "SemanticCompiler",
    "SemanticCompilerUnavailable",
    "SemanticError",
    "SemanticFieldDenied",
    "SemanticFilter",
    "SemanticGovernanceError",
    "SemanticModelRef",
    "SemanticQuery",
    "SemanticRequestError",
    "SemanticResource",
    "SemanticResourceError",
)

__all__ = (*__all__, "compile", "execute")
