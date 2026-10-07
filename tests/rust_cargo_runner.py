"""Run the qs_parsers crate's Rust unit tests from pytest (FEAT-180 validation contract).

Not a test module: imported by tests/test_rust_partial_match_units.py and the
tests/test_rust_pm_<dialect>_units.py wrappers (pytest puts tests/ on sys.path,
there is no tests/__init__.py).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_RESULT_RE = re.compile(r"test result: ok\. (\d+) passed")


def run_cargo_lib_tests(cargo_filter: str) -> int:
    """Run `cargo test --lib --no-default-features -- <cargo_filter>`; return tests passed.

    Skips when cargo is not installed. Fails (pytest.fail) on a non-zero exit code or
    when the filter matched zero tests — a filter that selects nothing proves nothing.
    """
    if shutil.which("cargo") is None:
        pytest.skip("cargo not installed")
    env = dict(os.environ, PYO3_PYTHON=sys.executable)
    libdir = sysconfig.get_config_var("LIBDIR") or ""
    env["LD_LIBRARY_PATH"] = os.pathsep.join(p for p in (libdir, env.get("LD_LIBRARY_PATH", "")) if p)
    proc = subprocess.run(
        ["cargo", "test", "--manifest-path", str(ROOT / "rust" / "Cargo.toml"), "--lib",
         "--no-default-features", "--", cargo_filter],
        capture_output=True, text=True, env=env, timeout=1800, check=False,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        pytest.fail(f"cargo test failed (exit {proc.returncode}):\n{output[-4000:]}")
    passed = sum(int(m) for m in _RESULT_RE.findall(output))
    if passed == 0:
        pytest.fail(f"cargo filter {cargo_filter!r} matched zero tests:\n{output[-2000:]}")
    return passed
