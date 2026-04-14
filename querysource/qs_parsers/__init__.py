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
    from ._qs_parsers import *  # noqa: F401,F403
    from . import _qs_parsers
    HAS_RUST = True
except ImportError:
    try:
        # Local dev (maturin develop): installed as top-level package
        from _qs_parsers import *  # noqa: F401,F403
        import _qs_parsers
        HAS_RUST = True
    except ImportError:
        HAS_RUST = False
