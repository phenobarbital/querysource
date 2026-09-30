"""An extension built before FEAT-156 (no ``sql_guard``) must not disable every Rust parser."""
import subprocess
import sys
import textwrap
from pathlib import Path

# Run the child from this checkout's root so it imports THIS tree's querysource, not whatever
# the current working directory happens to be (other tests may chdir).
REPO_ROOT = Path(__file__).resolve().parents[1]


def test_stale_extension_keeps_has_rust() -> None:
    """Import qs_parsers against a fake extension without ``sql_guard`` in a fresh interpreter."""
    code = textwrap.dedent(
        """
        import sys, types
        fake = types.ModuleType("querysource.qs_parsers._qs_parsers")
        fake.safe_format_map_validated = lambda *a, **k: ""
        sys.modules["querysource.qs_parsers._qs_parsers"] = fake
        import querysource.qs_parsers as q
        assert q.HAS_RUST is True, q.HAS_RUST
        assert q.sql_guard is None, q.sql_guard
        import querysource.interfaces.guarded_sql as g
        try:
            g.guard_statements("SELECT 1")
        except g.GuardedSQLError as err:
            assert err.category == "infra", err.category
        else:
            raise AssertionError("guard_statements must fail closed without sql_guard")
        print("ok")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=180, cwd=REPO_ROOT
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().endswith("ok")
