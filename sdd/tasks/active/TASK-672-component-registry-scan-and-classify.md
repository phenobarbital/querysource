# TASK-672: ComponentRegistry — Filesystem Scan + issubclass-Based Classification

**Feature**: FEAT-097 — New Destination Folder for MultiQuery
**Spec**: `sdd/specs/new-destination-multiquery.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-671
**Assigned-to**: unassigned

---

## Context

`ComponentRegistry.discover_all()` currently learns about destinations exclusively from `DESTINATION_REGISTRY` (`registry.py:147-152`) and `_classify()` falls back to a hardcoded name list (`registry.py:239`) because `AbstractDestination` had no `_category` until TASK-668. Now that:

1. `AbstractDestination._category == "Destinations"` (TASK-668), and
2. `queries/multi/destinations/` is a real folder hosting migrated classes (TASK-670),

`ComponentRegistry` can do TWO things: filesystem-scan the new folder for any `AbstractDestination` subclass, and merge with the existing `DESTINATION_REGISTRY` (new folder wins on collisions). `_classify` can drop the name-list special case in favor of a clean `issubclass(comp_cls, AbstractDestination)` check. Implements spec §3 Module 11 and §1 Goals 3-4.

---

## Scope

- Rewrite step (4) of `ComponentRegistry.discover_all()` (currently at `registry.py:147-152`):
  - Add a filesystem scan over `querysource/queries/multi/destinations/*.py` (skip `__*.py`, `_*.py`, `abstract.py`).
  - Import any class that (a) inherits `AbstractDestination`, (b) has `__module__` matching the scanned file's module. Register under `cls.__name__`.
  - Then merge with `from querysource.outputs.destinations import DESTINATION_REGISTRY` — new-folder entries overwrite legacy ones if any key collides.
- Rewrite `ComponentRegistry._classify()` (currently at `registry.py:222-241`):
  - Drop the name-list `if name in ("tableOutput", "TableOutput", "ToSharepoint", ...): return "Destinations"`.
  - First check `if isinstance(comp_cls, type) and issubclass(comp_cls, AbstractDestination): return "Destinations"`.
  - Keep the existing `ThreadSource` check for Sources.
  - The trailing fallback (`"Sources"` if name endswith `"Source"`, else `"Components"`) remains as a final defense.
- Ensure `ComponentRegistry.discover_all.cache_clear()` is called (or `functools.lru_cache(maxsize=1)` is otherwise reset) inside the test fixtures that exercise discovery so tests don't cross-contaminate.
- Add unit tests covering: scan picks up the four migrated classes; merge keeps `tableOutput`/`TableOutput`; `_classify` returns `"Destinations"` for any `AbstractDestination` subclass via `issubclass`; `get_catalog()` now returns non-empty `json_schema.properties` for every destination except `TableOutputAdapter` (which wraps non-introspectable `TableOutput`).

**NOT in scope**:
- Touching pipeline-runner logic in `MultiQS.query()` (spec §1 Non-Goals).
- Changing schema-generation logic — `SchemaIntrospectable` already does this.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/registry.py` | MODIFY | Rewrite destination discovery step + `_classify` |
| `tests/test_component_registry.py` | MODIFY | Add tests for scan/merge and `issubclass` classification |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# verified: querysource/queries/multi/registry.py:10
import functools
# verified: querysource/queries/multi/registry.py:11
import logging
# verified: querysource/queries/multi/registry.py:13
from pathlib import Path

# Pattern reference — sources/operators scan already lives here
# verified: querysource/queries/multi/registry.py:97-107

# At runtime — already used inside method bodies
from querysource.outputs.destinations import DESTINATION_REGISTRY  # registry.py:149
from querysource.outputs.destinations.abstract import AbstractDestination  # registry.py:231
```

### Existing Signatures to Modify
```python
# querysource/queries/multi/registry.py
class ComponentRegistry:                                        # line 64

    @classmethod
    @functools.lru_cache(maxsize=1)
    def discover_all(cls) -> dict[str, type]: ...               # line 82
    # Current destinations step:
    #     try:
    #         from querysource.outputs.destinations import DESTINATION_REGISTRY
    #         components.update(DESTINATION_REGISTRY)
    #     except (ImportError, AttributeError) as exc:
    #         logger.warning("Could not import DESTINATION_REGISTRY: %s", exc)
    # ↑ Replace with scan + merge.

    @classmethod
    def _classify(cls, name: str, comp_cls: type) -> str: ...   # line 222
    # Current body uses a hardcoded name list at line 239.

    @classmethod
    def get_catalog(cls) -> list[ComponentInfo]: ...            # line 156
    # This method is NOT modified — it already routes AbstractMulti subclasses
    # through get_schema/get_description and others through _classify + name.
    # After TASK-668 (AbstractDestination has the introspection methods), the
    # else-branch will produce empty schemas. We need destinations to go through
    # the if-branch by issubclass'ing AbstractMulti? No — they don't inherit
    # AbstractMulti, only SchemaIntrospectable. See "Special note" below.
```

### Special note on `get_catalog()`

`get_catalog()` currently dispatches on `issubclass(comp_cls, AbstractMulti)` (`registry.py:174`). After TASK-668 destinations have `get_schema()`/`get_description()` but they are NOT AbstractMulti subclasses — they inherit `SchemaIntrospectable` directly. To get populated schemas for destinations, change the dispatch condition to:

```python
from querysource.queries.multi._introspect import SchemaIntrospectable
# ...
if isinstance(comp_cls, type) and issubclass(comp_cls, SchemaIntrospectable):
    # Use introspection classmethods
    ...
```

This is the minimal correct change to honor acceptance criterion *"json_schema.properties is non-empty for every destination class"*.

### Does NOT Exist
- ~~`ComponentRegistry.discover_destinations()`~~ — there is no standalone destination-discovery method; the work lives inside `discover_all()`.
- ~~A separate cache for destinations~~ — the existing `@lru_cache(maxsize=1)` on `discover_all` covers everything.
- ~~`SchemaIntrospectable.is_destination`~~ — no such attribute. Classify via `issubclass(cls, AbstractDestination)`.
- ~~`AbstractDestination._is_multi`~~ — no such marker.

---

## Implementation Notes

### Pattern to Follow

```python
# Replace registry.py:147-152
try:
    from querysource.queries.multi.destinations import (
        DESTINATION_REGISTRY as _local_destinations,
    )
    # Filesystem-discovered MultiQS-local destinations win on key collision
    components.update(_local_destinations)
except (ImportError, AttributeError) as exc:
    logger.warning("Could not import queries.multi.destinations registry: %s", exc)

try:
    from querysource.outputs.destinations import DESTINATION_REGISTRY as _legacy_destinations
    # Merge: only add entries that aren't already provided by the new folder
    for step_name, cls in _legacy_destinations.items():
        components.setdefault(step_name, cls)
except (ImportError, AttributeError) as exc:
    logger.warning("Could not import legacy DESTINATION_REGISTRY: %s", exc)
```

Note: `queries/multi/destinations/__init__.py` already populates `DESTINATION_REGISTRY` via filesystem scan (TASK-669). We do NOT re-scan here — we reuse the registry it builds. The "scan + merge" criterion is satisfied because the new folder's `DESTINATION_REGISTRY` IS the scan result.

If the agent prefers an explicit scan inside `registry.py` instead of trusting the subpackage, both approaches are acceptable; use whichever results in less duplication. The simpler "import the subpackage's registry" version above is recommended.

### Pattern to Follow — `_classify`

```python
@classmethod
def _classify(cls, name: str, comp_cls: type) -> str:
    """Classify a component class into a category string."""
    try:
        from querysource.queries.multi.sources.base import ThreadSource
        if isinstance(comp_cls, type) and issubclass(comp_cls, ThreadSource):
            return "Sources"
    except (ImportError, AttributeError):
        pass
    try:
        from querysource.outputs.destinations.abstract import AbstractDestination
        if isinstance(comp_cls, type) and issubclass(comp_cls, AbstractDestination):
            return "Destinations"
    except (ImportError, AttributeError):
        pass
    # Heuristic from name (kept as final fallback only)
    if name.endswith("Source"):
        return "Sources"
    return "Components"
```

### Pattern to Follow — `get_catalog` dispatch

```python
from querysource.queries.multi._introspect import SchemaIntrospectable
# ...
if isinstance(comp_cls, type) and issubclass(comp_cls, SchemaIntrospectable):
    # Use introspection classmethods
    schema = comp_cls.get_schema()
    desc = comp_cls.get_description()
    # ...
else:
    # Fallback for non-introspectable classes (e.g. TableOutputAdapter wrapping TableOutput)
    # ...
```

### Key Constraints

- The `@functools.lru_cache(maxsize=1)` on `discover_all` means test fixtures must call `ComponentRegistry.discover_all.cache_clear()` between cases when the underlying registries are mutated. The existing test suite already does this — preserve that contract.
- DO NOT remove the hardcoded name list AND the heuristic name-endswith fallback in `_classify` at the same time without first verifying via `pytest tests/test_component_registry.py -v` that every existing case still classifies correctly. Keep the name-endswith fallback for `"*Source"` since it covers the edge case of sources registered without a ThreadSource ancestor.
- `TableOutputAdapter` inherits `AbstractDestination`, so after the `_classify` rewrite it will be classified as `"Destinations"` automatically — desired.
- `TableOutputAdapter` does NOT have meaningful introspection because its `__init__` proxies arbitrary kwargs to `TableOutput`. Its catalog entry will have `properties: {}`. The spec acceptance criterion explicitly allows this.

### References in Codebase
- `querysource/queries/multi/registry.py:97-107` — operator scan loop (reference shape).
- `querysource/queries/multi/sources/__init__.py:23-29` — `SOURCE_REGISTRY` static dict (reference).
- `tests/test_component_registry.py` — existing tests cover Sources / Operators / Destinations discovery; extend, don't rewrite.

---

## Acceptance Criteria

- [ ] `ComponentRegistry.discover_all()` returns a dict including:
  - `ToSharepoint`, `ToS3`, `TableDestination`, `DWHDestination` (from the new folder).
  - `tableOutput`, `TableOutput` (from the legacy registry — `TableOutputAdapter`).
  - All existing operators / transformations / sources (no regression).
- [ ] On a key collision between the new folder and the legacy registry, the new-folder class wins. (Test by registering a dummy entry in the legacy registry temporarily.)
- [ ] `ComponentRegistry._classify("ToSharepoint", ToSharepoint)` returns `"Destinations"` without using the hardcoded name list. (Verify by deleting the line that referenced the name list — the test must still pass.)
- [ ] `ComponentRegistry._classify("tableOutput", TableOutputAdapter)` returns `"Destinations"` (via `issubclass`).
- [ ] `ComponentRegistry.get_catalog()` returns `ComponentInfo` entries where:
  - For `ToSharepoint`, `ToS3`, `TableDestination`, `DWHDestination`: `json_schema["properties"]` is non-empty AND `category == "Destinations"`.
  - For `TableOutputAdapter` (`tableOutput`/`TableOutput`): empty `properties` is acceptable.
- [ ] All existing tests pass:
  - `pytest tests/test_component_registry.py -v`
  - `pytest tests/test_destination_*.py -v`
  - `pytest tests/test_multi_destinations_subpackage.py -v`
- [ ] `ruff check querysource/queries/multi/registry.py` — no errors.
- [ ] `mypy querysource/queries/multi/registry.py` — no new errors (compare against pre-task baseline if there are pre-existing warnings).

---

## Test Specification

Append to `tests/test_component_registry.py`:

```python
class TestDestinationDiscovery:
    def setup_method(self):
        from querysource.queries.multi.registry import ComponentRegistry
        ComponentRegistry.discover_all.cache_clear()

    def test_scan_picks_up_migrated_destinations(self):
        from querysource.queries.multi.registry import ComponentRegistry
        components = ComponentRegistry.discover_all()
        for cls_name in ("ToSharepoint", "ToS3", "TableDestination", "DWHDestination"):
            assert cls_name in components, f"{cls_name} missing from discover_all()"

    def test_merge_preserves_legacy_step_names(self):
        from querysource.queries.multi.registry import ComponentRegistry
        components = ComponentRegistry.discover_all()
        for key in ("tableOutput", "TableOutput"):
            assert key in components, f"Legacy registry key '{key}' missing"

    def test_classify_destinations_via_issubclass(self):
        from querysource.queries.multi.registry import ComponentRegistry
        from querysource.queries.multi.destinations.sharepoint import ToSharepoint
        assert ComponentRegistry._classify("ToSharepoint", ToSharepoint) == "Destinations"

    def test_classify_table_output_adapter_via_issubclass(self):
        from querysource.queries.multi.registry import ComponentRegistry
        from querysource.outputs.destinations import TableOutputAdapter
        assert ComponentRegistry._classify("tableOutput", TableOutputAdapter) == "Destinations"

    def test_catalog_returns_populated_schema_for_real_destinations(self):
        from querysource.queries.multi.registry import ComponentRegistry
        catalog = {ci.name: ci for ci in ComponentRegistry.get_catalog()}
        for name in ("ToSharepoint", "ToS3", "TableDestination", "DWHDestination"):
            ci = catalog[name]
            assert ci.category == "Destinations"
            assert ci.json_schema.get("properties"), (
                f"Expected populated JSON schema for {name}; got {ci.json_schema}"
            )

    def test_catalog_allows_empty_schema_for_table_output_adapter(self):
        """TableOutputAdapter wraps a non-introspectable TableOutput; empty is OK."""
        from querysource.queries.multi.registry import ComponentRegistry
        catalog = {ci.name: ci for ci in ComponentRegistry.get_catalog()}
        # Either it's listed under "tableOutput" or "TableOutput" — both keys point at the adapter
        adapter_entry = catalog.get("tableOutput") or catalog.get("TableOutput")
        assert adapter_entry is not None
        assert adapter_entry.category == "Destinations"
```

---

## Agent Instructions

1. **Read the spec** at `sdd/specs/new-destination-multiquery.spec.md` (§3 Module 11, §6 Codebase Contract).
2. **Check dependencies** — TASK-671 in `sdd/tasks/completed/`.
3. **Verify the Codebase Contract** — `grep -n "DESTINATION_REGISTRY\|_classify" querysource/queries/multi/registry.py` matches the listed line numbers, modulo the small drift that TASK-668-671 introduced (none expected here).
4. **Update status** in `sdd/tasks/index/new-destination-multiquery.json` → `"in-progress"`.
5. **Implement** — rewrite `discover_all` step 4, rewrite `_classify`, switch `get_catalog` dispatch to `SchemaIntrospectable`.
6. **Verify** — full `tests/test_component_registry.py` and `tests/test_destination_*.py`. Make sure no operator/transformation/source test breaks.
7. **Move** this file to `sdd/tasks/completed/TASK-672-component-registry-scan-and-classify.md`.
8. **Update index** → `"done"`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
