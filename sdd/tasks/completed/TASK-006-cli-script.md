# TASK-006: CLI Script for Documentation Generation

**Feature**: Component Documentation API
**Spec**: `sdd/specs/component-documentation-api.spec.md`
**Status**: done
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-005
**Assigned-to**: claude-session-2026-03-03

---

## Context

This task creates a CLI script to trigger documentation generation. Since flowtask doesn't have a CLI framework with subcommands, this will be a standalone script invoked via `python -m flowtask.documentation.cli` or a similar entry point.

Reference: Spec Section 3 - Module 4: CLI Command

---

## Scope

- Create CLI script for documentation generation
- Accept `--output` option to specify output directory (default: `documentation/`)
- Accept `--components` option to specify component directories to scan
- Print progress and summary to stdout
- Support `--help` with usage information

**NOT in scope**:
- HTTP API (TASK-007)
- Incremental/differential generation (future enhancement)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `flowtask/documentation/cli.py` | CREATE | CLI script implementation |
| `flowtask/documentation/__init__.py` | MODIFY | Ensure cli is importable |
| `pyproject.toml` | MODIFY | Add entry point for CLI (optional) |
| `tests/unit/documentation/test_cli.py` | CREATE | Unit tests for CLI |

---

## Implementation Notes

### Pattern to Follow
```python
# flowtask/documentation/cli.py
"""CLI for generating component documentation.

Usage:
    python -m flowtask.documentation.cli [--output DIR] [--components DIR...]
"""
import argparse
import sys
import logging
from pathlib import Path
from navconfig import BASE_DIR

from .generator import ComponentDocGenerator


def main(args=None):
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description="Generate documentation for Flowtask components",
        prog="flowtask-docs"
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=BASE_DIR / "documentation",
        help="Output directory for generated docs (default: documentation/)"
    )
    parser.add_argument(
        "--components", "-c",
        type=Path,
        nargs="+",
        default=None,
        help="Component directories to scan (default: flowtask/components, plugins/components)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )

    parsed = parser.parse_args(args)

    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if parsed.verbose else logging.INFO,
        format="%(levelname)s: %(message)s"
    )
    logger = logging.getLogger(__name__)

    # Default component paths
    if parsed.components is None:
        flowtask_components = BASE_DIR / "flowtask" / "components"
        plugins_components = BASE_DIR / "plugins" / "components"
        component_paths = [flowtask_components, plugins_components]
    else:
        component_paths = parsed.components

    logger.info(f"Output directory: {parsed.output}")
    logger.info(f"Scanning: {[str(p) for p in component_paths]}")

    # Generate docs
    generator = ComponentDocGenerator(output_dir=parsed.output)
    index = generator.generate(component_paths)

    # Summary
    count = len(index.components)
    logger.info(f"Generated documentation for {count} components")
    logger.info(f"Index written to: {parsed.output / 'index.json'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### Key Constraints
- Use `argparse` for CLI parsing (standard library, no extra deps)
- Use `navconfig.BASE_DIR` to determine project root
- Print human-readable progress messages
- Return exit code 0 on success, non-zero on error
- Handle keyboard interrupt gracefully

### References in Codebase
- `flowtask/__main__.py` — existing entry point pattern

---

## Acceptance Criteria

- [x] CLI script runs with `python -m flowtask.documentation.cli`
- [x] `--help` shows usage information
- [x] `--output` specifies output directory
- [x] `--components` specifies directories to scan
- [x] Default paths are `flowtask/components` and `plugins/components`
- [x] Progress output shows number of components processed
- [x] Exit code 0 on success
- [x] All tests pass: `pytest tests/unit/documentation/test_cli.py -v`

---

## Test Specification

```python
# tests/unit/documentation/test_cli.py
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from flowtask.documentation.cli import main


class TestCLI:
    def test_main_with_help(self, capsys):
        """--help shows usage and exits."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "Generate documentation" in captured.out

    def test_main_default_paths(self, tmp_path):
        """Default component paths are used when not specified."""
        with patch("flowtask.documentation.cli.ComponentDocGenerator") as mock_gen:
            mock_instance = MagicMock()
            mock_instance.generate.return_value = MagicMock(components={})
            mock_gen.return_value = mock_instance

            main(["--output", str(tmp_path)])

            # Verify generate was called
            mock_instance.generate.assert_called_once()

    def test_main_custom_output(self, tmp_path):
        """Custom output directory is used."""
        output = tmp_path / "custom_docs"
        with patch("flowtask.documentation.cli.ComponentDocGenerator") as mock_gen:
            mock_instance = MagicMock()
            mock_instance.generate.return_value = MagicMock(components={})
            mock_gen.return_value = mock_instance

            main(["--output", str(output)])

            mock_gen.assert_called_once_with(output_dir=output)

    def test_main_returns_zero(self, tmp_path):
        """Main returns 0 on success."""
        with patch("flowtask.documentation.cli.ComponentDocGenerator") as mock_gen:
            mock_instance = MagicMock()
            mock_instance.generate.return_value = MagicMock(components={})
            mock_gen.return_value = mock_instance

            result = main(["--output", str(tmp_path)])

            assert result == 0
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/component-documentation-api.spec.md` for full context
2. **Check dependencies** — verify TASK-005 is in `sdd/tasks/completed/`
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"` with your session ID
4. **Implement** following the scope and notes above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-006-cli-script.md`
7. **Update index** → `"done"`
8. **Fill in the Completion Note** below

---

## Completion Note

**Completed by**: claude-session-2026-03-03
**Date**: 2026-03-03
**Notes**:
- Created `flowtask/documentation/cli.py` with full CLI implementation
- Created `tests/unit/documentation/test_cli.py` with 20 comprehensive tests
- All 20 tests pass successfully
- CLI generates documentation for 113 components in production codebase
- Added `--quiet` flag beyond spec for suppressing non-error output
- Handles keyboard interrupt gracefully with exit code 130
- Fixed path resolution bug in generator.py for absolute paths

**Deviations from spec**:
- Added `--quiet/-q` flag for suppressing output (useful for scripting)
- Changed `--help` handling to return exit code via main() instead of raising SystemExit
- Uses try/except for navconfig import with fallback to cwd (more portable)
