"""Locate the ``ai-parrot`` runtime that backs the SDD dev-loop tooling.

Several SDD scripts ported from ai-parrot (``finalize_task``,
``review_checkpoint``, the ``close_task.sh`` ledger emission) read or write
the durable state owned by the ``parrot-sdd-coder`` MCP server, so they must
import the *same* ``parrot`` package that server runs. QuerySource does not
depend on ``ai-parrot``; instead the server is launched from the ai-parrot
checkout's virtualenv (see ``.mcp.json``). This module resolves that
interpreter and, when the current one cannot import ``parrot``, re-executes
the calling module under it.

Resolution order:

1. ``SDD_PARROT_PYTHON`` environment variable (absolute path to a python).
2. The ``parrot-sdd-coder`` server ``command`` in the main checkout's
   ``.mcp.json`` — its sibling ``python`` in the same ``bin/`` directory.

Usage::

    python -m scripts.sdd._parrot_runtime --print-python
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

_ENV_VAR = "SDD_PARROT_PYTHON"
_REEXEC_GUARD = "SDD_PARROT_REEXEC"
_MCP_SERVER = "parrot-sdd-coder"


def _main_checkout(start: Path) -> Path | None:
    """Return the main checkout root (owner of ``.git``) for ``start``, or None outside git."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=start,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return Path(out.stdout.strip()).parent


def resolve_parrot_python(start: Path | None = None) -> Path | None:
    """Resolve the python interpreter of the ai-parrot runtime.

    Args:
        start: Directory used to locate the main checkout (defaults to cwd).

    Returns:
        Path to an existing python executable, or None when none is configured.
    """
    env = os.environ.get(_ENV_VAR)
    if env:
        candidate = Path(env)
        return candidate if candidate.exists() else None
    root = _main_checkout(start or Path.cwd())
    if root is None:
        return None
    mcp = root / ".mcp.json"
    try:
        servers = json.loads(mcp.read_text(encoding="utf-8")).get("mcpServers", {})
    except (OSError, ValueError):
        return None
    command = (servers.get(_MCP_SERVER) or {}).get("command")
    if not command:
        return None
    candidate = Path(command).parent / "python"
    return candidate if candidate.exists() else None


def ensure_parrot(module: str, script: Path | None = None) -> None:
    """Re-exec the caller under the ai-parrot runtime when ``parrot`` is missing.

    Returns normally when ``parrot`` is already importable. Exits with status 2
    and an explanatory message when no runtime can be resolved.

    Args:
        module: Dotted module name of the caller (e.g. ``scripts.sdd.finalize_task``).
        script: When the caller is run by path (``python scripts/x.py``), its
            file path; it is re-executed by path instead of with ``-m``.
    """
    if importlib.util.find_spec("parrot") is not None:
        return
    python = resolve_parrot_python(script.parent if script is not None else Path(__file__).resolve().parent)
    if python is None or os.environ.get(_REEXEC_GUARD):
        sys.stderr.write(
            f"{module}: the 'parrot' package is not importable and no ai-parrot runtime was found.\n"
            f"Set {_ENV_VAR}=/path/to/ai-parrot/.venv/bin/python or configure the "
            f"'{_MCP_SERVER}' server in .mcp.json.\n"
        )
        raise SystemExit(2)
    os.environ[_REEXEC_GUARD] = "1"
    target = [str(script)] if script is not None else ["-m", module]
    os.execv(str(python), [str(python), *target, *sys.argv[1:]])


def main(argv: list[str] | None = None) -> int:
    """CLI: print the resolved interpreter (exit 1 when none)."""
    parser = argparse.ArgumentParser(description="Locate the ai-parrot runtime interpreter.")
    parser.add_argument("--print-python", action="store_true", help="print the resolved interpreter path")
    parser.parse_args(argv)
    python = resolve_parrot_python()
    if python is None:
        return 1
    sys.stdout.write(f"{python}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
