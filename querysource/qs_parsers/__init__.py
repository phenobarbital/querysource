# Copyright (C) 2018-present Jesus Lara
#
# querysource/qs_parsers/__init__.py
"""Rust-accelerated parser functions for QuerySource.

Tries to import the compiled Rust extension (_qs_parsers).
Falls back gracefully when the extension is not available
(e.g. pure-Python / sdist install).
"""
try:
    # In-wheel location: .so bundled inside querysource/qs_parsers/
    from . import _qs_parsers
    from ._qs_parsers import *  # noqa: F401,F403

    # Explicit re-export for context-aware validating substitution (FEAT-103)
    from ._qs_parsers import safe_format_map_validated  # noqa: F401
    HAS_RUST = True
except ImportError:
    try:
        # Local dev (maturin develop): installed as top-level package
        import _qs_parsers
        from _qs_parsers import *  # noqa: F401,F403

        # Explicit re-export for context-aware validating substitution (FEAT-103)
        from _qs_parsers import safe_format_map_validated  # noqa: F401
        HAS_RUST = True
    except ImportError:
        HAS_RUST = False

# ExecuteSQL statement guard (FEAT-156). Resolved separately from the block above, so that an
# extension built before FEAT-156 (no ``sql_guard``) does not turn HAS_RUST off for every other
# Rust parser. Callers such as ``querysource.interfaces.guarded_sql`` treat None as unavailable
# and fail closed.
sql_guard = getattr(_qs_parsers, "sql_guard", None) if HAS_RUST else None
