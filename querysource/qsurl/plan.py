"""The in-memory work a provider did not do for a qsurl query."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResidualPlan:
    """Work left for the residual stage.

    Applied by ``querysource.qsurl.residual.apply`` in the fixed order
    filter -> sort -> project -> distinct -> offset -> limit -> rename.

    Attributes:
        filter: IR filter node or leaf still to evaluate, or None.
        sort: ``(column, descending)`` pairs.
        project: final column order (original names); empty keeps every column.
        distinct: drop duplicate rows.
        offset: rows to skip.
        limit: rows to keep.
        rename: ``(column, alias)`` pairs, applied last.
    """

    filter: dict | None = None
    sort: tuple[tuple[str, bool], ...] = ()
    project: tuple[str, ...] = ()
    distinct: bool = False
    offset: int | None = None
    limit: int | None = None
    rename: tuple[tuple[str, str], ...] = ()

    def is_empty(self) -> bool:
        """Return True when every field is at its default (nothing to apply)."""
        return self == ResidualPlan()
