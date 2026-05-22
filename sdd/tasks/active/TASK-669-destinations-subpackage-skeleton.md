# TASK-669: Create queries/multi/destinations/ Subpackage Skeleton

**Feature**: FEAT-097 — New Destination Folder for MultiQuery
**Spec**: `sdd/specs/new-destination-multiquery.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-668
**Assigned-to**: unassigned

---

## Context

`queries/multi/destinations/` does not exist yet. Create the subpackage with a shim re-export of `AbstractDestination` and a local `DESTINATION_REGISTRY` built by scanning the folder. At this point the folder contains no concrete destinations — those land in TASK-670 — so `DESTINATION_REGISTRY` is initially empty. Implements spec §3 Module 4.

---

## Scope

- Create `querysource/queries/multi/destinations/__init__.py` that:
  1. Re-exports `AbstractDestination` from `querysource.outputs.destinations.abstract` (shim).
  2. Defines `DESTINATION_REGISTRY: dict[str, type[AbstractDestination]] = {}`.
  3. Scans its own folder for `*.py` files (skipping `__init__.py`, `_*.py`, `abstract.py`) and imports any class that inherits `AbstractDestination`, registering it under its `__name__`. Optional-dep import failures are caught and logged at debug level (mirroring `outputs/destinations/__init__.py:62-91`).
  4. Exposes `__all__ = ("AbstractDestination", "DESTINATION_REGISTRY")`.
- Add a focused unit test that imports `AbstractDestination` from the new path and asserts it is the same object as the one imported from the old path.

**NOT in scope**:
- Moving any concrete destination class into the folder (TASK-670).
- Wiring this registry into `ComponentRegistry.discover_all` (TASK-672).
- Modifying `outputs/destinations/__init__.py` (TASK-671).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/destinations/__init__.py` | CREATE | Shim re-export + folder-scan local registry |
| `tests/test_multi_destinations_subpackage.py` | CREATE | Unit test for shim and empty-registry scan |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# verified: querysource/outputs/destinations/abstract.py:17
from querysource.outputs.destinations.abstract import AbstractDestination

# Pattern reference — current registry's optional-dep guard
# verified: querysource/outputs/destinations/__init__.py:62-91
```

### Existing Signatures to Use
```python
# Filesystem-scan pattern — mirrors querysource/queries/multi/registry.py:97-107
# (the operator loop). Use the same idiom:
operators_dir = Path(__file__).parent / "operators"
for py_file in sorted(operators_dir.glob("*.py")):
    if py_file.name.startswith("_") or py_file.name == "abstract.py":
        continue
    clsname = py_file.stem
    # importlib.import_module(...) + getattr(...)
```

### Existing Optional-Dep Pattern
```python
# querysource/outputs/destinations/__init__.py:62-67 — copy this idiom
try:
    from .sharepoint import ToSharepoint
    DESTINATION_REGISTRY["ToSharepoint"] = ToSharepoint
except ImportError:
    _pkg_logger.debug(
        "ToSharepoint destination not available: msgraph-sdk or azure-identity not installed"
    )
```

### Does NOT Exist
- ~~`querysource.queries.multi.destinations`~~ — package created by this task.
- ~~`querysource.queries.multi.destinations.abstract`~~ — `AbstractDestination` is NOT re-exported into a child `abstract` module. Only into the package `__init__`. Concrete destinations (added in TASK-670) will import via `from . import AbstractDestination`.
- ~~`get_destination` in this new package~~ — only the legacy one in `outputs/destinations/` exists (see spec §8 — defaulting to "rely on the legacy one"). Do not add a duplicate `get_destination` here.

---

## Implementation Notes

### Pattern to Follow

```python
# querysource/queries/multi/destinations/__init__.py
"""
querysource.queries.multi.destinations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Canonical home for MultiQuery-only destination components.

This package re-exports :class:`AbstractDestination` (defined in
:mod:`querysource.outputs.destinations.abstract` for Flowtask compatibility)
and exposes a folder-scanned :data:`DESTINATION_REGISTRY` of every destination
class living under this package.

The legacy registry at :mod:`querysource.outputs.destinations` continues to
host :class:`TableOutputAdapter` (Flowtask-shared) and aggregates entries from
this package via backward-compat shims.
"""
from __future__ import annotations

import importlib
import inspect
import logging as _logging
from pathlib import Path

from querysource.outputs.destinations.abstract import AbstractDestination

_pkg_logger = _logging.getLogger(__name__)


def _scan_destinations() -> dict[str, type[AbstractDestination]]:
    """Scan this folder for AbstractDestination subclasses and return a registry."""
    registry: dict[str, type[AbstractDestination]] = {}
    pkg_dir = Path(__file__).parent
    for py_file in sorted(pkg_dir.glob("*.py")):
        name = py_file.name
        if name.startswith("_") or name == "abstract.py":
            continue
        stem = py_file.stem
        try:
            module = importlib.import_module(
                f".{stem}", package="querysource.queries.multi.destinations"
            )
        except ImportError as exc:
            _pkg_logger.debug(
                "Destination module '%s' skipped (optional dep missing): %s",
                stem, exc,
            )
            continue
        for cls_name, obj in inspect.getmembers(module, inspect.isclass):
            if obj is AbstractDestination:
                continue
            if issubclass(obj, AbstractDestination) and obj.__module__ == module.__name__:
                registry[cls_name] = obj
    return registry


DESTINATION_REGISTRY: dict[str, type[AbstractDestination]] = _scan_destinations()


__all__ = ("AbstractDestination", "DESTINATION_REGISTRY")
```

### Key Constraints

- Scan must be deterministic — use `sorted(glob(...))`.
- Skip subclasses imported from other modules: the `obj.__module__ == module.__name__` filter prevents `AbstractDestination` (re-imported) or future utility classes from accidentally being registered.
- Optional-dep tolerance: a missing optional dependency (e.g. `aioboto3`) must NOT crash the scan — log and continue.
- Do NOT call `_scan_destinations()` more than once per process (Python imports the package once, so module-level call is fine — do not memoize beyond that).
- `DESTINATION_REGISTRY` must be a real dict (not a property/function) so it can be mutated by tests via `monkeypatch` if needed.

### References in Codebase
- `querysource/outputs/destinations/__init__.py` — registry pattern (TableOutputAdapter, try/except per-import).
- `querysource/queries/multi/sources/__init__.py` — SOURCE_REGISTRY pattern (static, but similar `__all__` shape).
- `querysource/queries/multi/registry.py:97-107` — filesystem scan pattern.

---

## Acceptance Criteria

- [ ] `querysource/queries/multi/destinations/__init__.py` exists and is importable.
- [ ] `from querysource.queries.multi.destinations import AbstractDestination` resolves to the same class object as `from querysource.outputs.destinations.abstract import AbstractDestination`.
- [ ] `from querysource.queries.multi.destinations import DESTINATION_REGISTRY` resolves; at this point in the task graph it is an empty dict (concrete classes are added by TASK-670).
- [ ] `python -c "import querysource.queries.multi.destinations"` does not raise.
- [ ] No circular import: `python -c "import querysource.queries.multi; import querysource.outputs.destinations"` runs cleanly.
- [ ] `ruff check querysource/queries/multi/destinations/` — no errors.
- [ ] New test `tests/test_multi_destinations_subpackage.py` passes.

---

## Test Specification

```python
# tests/test_multi_destinations_subpackage.py
"""Subpackage skeleton — TASK-669."""
import pytest


def test_shim_reexports_abstract_destination():
    from querysource.queries.multi.destinations import AbstractDestination as ADestNew
    from querysource.outputs.destinations.abstract import AbstractDestination as ADestOrig
    assert ADestNew is ADestOrig


def test_registry_is_dict():
    from querysource.queries.multi.destinations import DESTINATION_REGISTRY
    assert isinstance(DESTINATION_REGISTRY, dict)


def test_registry_starts_empty_or_only_contains_abstractdestination_subclasses():
    """At this stage no concrete destinations have been moved here yet (TASK-670).

    Should the test run after TASK-670 lands, the registry will contain
    ToSharepoint, ToS3, TableDestination, DWHDestination. In either case
    every entry must be an AbstractDestination subclass.
    """
    from querysource.queries.multi.destinations import (
        AbstractDestination,
        DESTINATION_REGISTRY,
    )
    for name, cls in DESTINATION_REGISTRY.items():
        assert issubclass(cls, AbstractDestination), (
            f"{name} is registered but does not inherit AbstractDestination"
        )
```

---

## Agent Instructions

1. **Read the spec** at `sdd/specs/new-destination-multiquery.spec.md` (§2 Overview, §3 Module 4).
2. **Check dependencies** — TASK-668 must be in `sdd/tasks/completed/`.
3. **Verify the Codebase Contract** — confirm `AbstractDestination` inherits `SchemaIntrospectable` (proof that TASK-668 landed).
4. **Update status** in `sdd/tasks/index/new-destination-multiquery.json` → `"in-progress"`.
5. **Implement** — create the folder + `__init__.py`; write the unit test.
6. **Verify** — run `pytest tests/test_multi_destinations_subpackage.py -v`. Also run `pytest tests/test_destination_*.py -v` to confirm nothing else broke.
7. **Move** this file to `sdd/tasks/completed/TASK-669-destinations-subpackage-skeleton.md`.
8. **Update index** → `"done"`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
