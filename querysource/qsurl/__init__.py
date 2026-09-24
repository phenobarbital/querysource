"""qsurl — HTSQL-style URL query dialect for QuerySource.

Public API: ``parse()``, ``requires()``, ``to_gbnf()``, ``HAS_RUST``, ``QSUrlError``,
``ResidualPlan``. The Rust extension (``_qsurl``) is used when importable; otherwise
the pure-Python Lark fallback is imported lazily on first use.
"""
from __future__ import annotations

import json
import logging
import os

from .errors import QSUrlError
from .plan import ResidualPlan

_logger = logging.getLogger(__name__)
_FORCE_FALLBACK = os.environ.get("QSURL_FORCE_FALLBACK", "") == "1"

_rs = None
if not _FORCE_FALLBACK:
    try:
        from . import _qsurl as _rs  # in-wheel: querysource/qsurl/_qsurl*.so
    except ImportError:
        try:
            import _qsurl as _rs  # maturin develop installs it top-level
        except ImportError:
            _rs = None
HAS_RUST: bool = _rs is not None
_warned = False


def _fallback():
    """Import the Lark back-end on first use, warning once per process."""
    global _warned  # pylint: disable=global-statement
    from . import _fallback as fb  # lazy: created by TASK-766
    if not _warned:
        _logger.warning("qsurl: Rust extension unavailable, using the Lark fallback")
        _warned = True
    return fb


def parse(src: str) -> dict:
    """Parse a percent-decoded qsurl string into the IR dict.

    Args:
        src: the percent-decoded qsurl source string.

    Returns:
        The IR dict (see spec Data Models).

    Raises:
        QSUrlError: kind "parse" or "lower", from either back-end.
    """
    if _rs is not None:
        try:
            return json.loads(_rs.parse(src))
        except ValueError as err:
            raise QSUrlError.from_json(str(err.args[0])) from err
    return _fallback().parse(src)


def requires(src: str) -> list[str]:
    """Return the IR's ``requires`` list (Rust declaration order); raises QSUrlError."""
    return list(parse(src)["requires"])


def to_gbnf() -> str:
    """Return the GBNF rendering of ``grammar.lark`` (lazy import of ``.gbnf``)."""
    from .gbnf import to_gbnf as _to_gbnf  # created by TASK-768
    return _to_gbnf()


__all__ = ("HAS_RUST", "QSUrlError", "ResidualPlan", "parse", "requires", "to_gbnf")
