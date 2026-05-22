# TASK-688: Write Unit and Integration Tests for tExplode

**Feature**: FEAT-099 — MultiQS New Component — tExplode
**Spec**: `sdd/specs/multiqs-new-component-explode.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-687
**Assigned-to**: unassigned

---

## Context

This task writes comprehensive tests for the `tExplode` transformation
component created in TASK-687. It covers both standard and advanced modes,
dict-of-DataFrames input, error handling, and integration with the
`ComponentRegistry` and `get_transform_module` discovery system.
Implements Spec §3 Module 2 and §4 Test Specification.

---

## Scope

- Create `tests/test_texplode.py` with all unit tests from the spec (§4).
- Write fixtures: `df_with_lists`, `df_with_dicts`, `df_with_empty_lists`.
- Cover standard mode: list explode, dict normalize, drop_original toggle.
- Cover advanced mode: parent tracking, propagate_columns, empty list preservation.
- Cover dict-of-DataFrames input.
- Cover error conditions: missing column kwarg, empty DataFrame, non-existent column.
- Cover async context manager usage.
- Write integration tests: registry discovery, introspection schema, transform chain.
- All tests must use `pytest-asyncio` for async test support.

**NOT in scope**:
- Modifying the `tExplode` implementation (TASK-687).
- Modifying any existing test files.
- Performance benchmarks.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_texplode.py` | CREATE | Unit and integration tests |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase.
> The implementing agent MUST use these exact imports, class names, and method signatures.
> **DO NOT** invent, guess, or assume any import, attribute, or method not listed here.

### Verified Imports

```python
# For the test file
import pytest                                           # test framework
import pandas as pd                                     # DataFrame creation for fixtures
from pandas import json_normalize                       # for assertion verification

# Component under test (created by TASK-687)
from querysource.queries.multi.transformations.tExplode import tExplode

# Exceptions for error-case assertions
from querysource.exceptions import (                    # verified: querysource/exceptions.py:6,48,58
    DataNotFound,
    DriverError,
    QueryException
)

# For integration tests
from querysource.queries.multi import get_transform_module  # verified: querysource/queries/multi/__init__.py:37
from querysource.queries.multi.registry import ComponentRegistry  # verified: querysource/queries/multi/registry.py
```

### Existing Signatures to Use

```python
# querysource/queries/multi/transformations/abstract.py:16
class AbstractTransform(AbstractMulti):
    _category = "Transformations"                       # line 25
    async def start(self):                              # line 39 — validates input
    async def run(self):                                # line 59 (abstract)

# querysource/queries/multi/__init__.py:37
def get_transform_module(clsname: str):                 # returns the class
    # get_transform_module("tExplode") → tExplode class

# querysource/queries/multi/registry.py (class)
class ComponentRegistry:
    @classmethod
    def discover_all(cls) -> dict:                      # line ~119 — returns {name: class}
```

### tExplode Signatures (from TASK-687 — verify after TASK-687 completes)

```python
# querysource/queries/multi/transformations/tExplode.py (created by TASK-687)
class tExplode(AbstractTransform):
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        # kwargs: column (str, required), drop_original (bool), explode_dataset (bool),
        #         advanced_mode (bool), propagate_columns (list)
    async def run(self) -> Union[dict, pd.DataFrame]:
        # Explodes column, returns DataFrame or dict of DataFrames
```

### Does NOT Exist

- ~~`tExplode.explode()`~~ — no such public method; call `run()` via async context manager
- ~~`tExplode.start()`~~ — inherited from `AbstractTransform`, called internally by `run()`
- ~~`querysource.queries.multi.transformations.tExplode.tExplode.validate()`~~ — no such method
- ~~`ComponentRegistry.register()`~~ — no manual registration; discovery is automatic via glob

---

## Implementation Notes

### Test Pattern — Async Tests

```python
import pytest

@pytest.mark.asyncio
async def test_texplode_basic():
    df = pd.DataFrame({"id": [1, 2], "tags": [["a", "b"], ["c"]]})
    obj = tExplode(data=df, column="tags")
    async with obj as t:
        result = await t.run()
    assert len(result) == 3  # 2 from row 1 + 1 from row 2
```

### Test Pattern — Error Cases

```python
@pytest.mark.asyncio
async def test_texplode_missing_column_raises():
    df = pd.DataFrame({"id": [1]})
    with pytest.raises(DriverError):
        tExplode(data=df)  # no column= kwarg → DriverError in __init__
```

### Key Constraints

- Use `@pytest.mark.asyncio` on all async test functions
- Use `async with tExplode(...) as t: result = await t.run()` pattern
- Verify DataFrame shapes, column names, and values — not just non-empty
- Test both single DataFrame and dict-of-DataFrames inputs
- For the registry test, call `ComponentRegistry.discover_all()` and check `"tExplode"` is in the result

### References in Codebase

- `tests/test_abstract_multi.py` — example of testing MultiQuery components
- `querysource/queries/multi/transformations/Map.py` — reference transform for pattern comparison
- `querysource/queries/multi/transformations/tOrder.py` — reference transform for pattern comparison

---

## Acceptance Criteria

- [ ] `tests/test_texplode.py` exists with all 13 unit tests from spec §4
- [ ] 3 integration tests: registry discovery, introspection schema, transform chain
- [ ] All fixtures from spec §4 are implemented
- [ ] All tests pass: `pytest tests/test_texplode.py -v`
- [ ] No linting errors: `ruff check tests/test_texplode.py`
- [ ] Tests cover both standard and advanced modes
- [ ] Tests cover dict-of-DataFrames input
- [ ] Tests cover error conditions (missing column, empty df, non-existent column)

---

## Test Specification

### Unit Tests (from Spec §4)

```python
import pytest
import pandas as pd
from querysource.queries.multi.transformations.tExplode import tExplode
from querysource.exceptions import DataNotFound, DriverError


@pytest.fixture
def df_with_lists():
    return pd.DataFrame({
        "id": [1, 2, 3],
        "tags": [["a", "b"], ["c"], ["d", "e", "f"]],
        "name": ["Alice", "Bob", "Carol"]
    })


@pytest.fixture
def df_with_dicts():
    return pd.DataFrame({
        "id": [1, 2],
        "details": [
            {"color": "red", "size": 10},
            {"color": "blue", "size": 20}
        ],
        "name": ["Alice", "Bob"]
    })


@pytest.fixture
def df_with_empty_lists():
    return pd.DataFrame({
        "id": [1, 2, 3],
        "items": [["x", "y"], [], ["z"]],
        "group": ["A", "B", "C"]
    })


class TestTExplodeInit:
    def test_texplode_init_requires_column(self):
        """Raises DriverError when column kwarg is missing."""
        df = pd.DataFrame({"id": [1]})
        with pytest.raises(DriverError):
            tExplode(data=df)


class TestTExplodeStandardMode:
    @pytest.mark.asyncio
    async def test_texplode_basic_list_explode(self, df_with_lists):
        """Explodes a column of lists into rows."""
        ...

    @pytest.mark.asyncio
    async def test_texplode_dict_explode_with_normalize(self, df_with_dicts):
        """Explodes + json_normalize when explode_dataset=True."""
        ...

    @pytest.mark.asyncio
    async def test_texplode_drop_original(self, df_with_lists):
        """Source column is removed when drop_original=True."""
        ...

    @pytest.mark.asyncio
    async def test_texplode_no_drop_original(self, df_with_lists):
        """Source column is preserved when drop_original=False."""
        ...

    @pytest.mark.asyncio
    async def test_texplode_explode_dataset_false(self, df_with_dicts):
        """Dicts stay as values when explode_dataset=False."""
        ...


class TestTExplodeAdvancedMode:
    @pytest.mark.asyncio
    async def test_texplode_advanced_mode_basic(self, df_with_lists):
        """Advanced mode tracks parent index, explodes non-empty lists."""
        ...

    @pytest.mark.asyncio
    async def test_texplode_advanced_propagate_columns(self, df_with_dicts):
        """Parent columns propagated to child rows in advanced mode."""
        ...

    @pytest.mark.asyncio
    async def test_texplode_advanced_empty_lists_preserved(self, df_with_empty_lists):
        """Rows with empty lists are kept in advanced mode."""
        ...


class TestTExplodeEdgeCases:
    @pytest.mark.asyncio
    async def test_texplode_dict_of_dataframes(self):
        """Handles dict-of-DataFrames input."""
        ...

    @pytest.mark.asyncio
    async def test_texplode_empty_dataframe(self):
        """Raises DataNotFound on empty input."""
        ...

    @pytest.mark.asyncio
    async def test_texplode_column_not_found(self):
        """Raises DriverError when column doesn't exist in DataFrame."""
        ...

    @pytest.mark.asyncio
    async def test_texplode_async_context_manager(self, df_with_lists):
        """Works correctly via async with tExplode(...) as t: await t.run()."""
        ...


class TestTExplodeIntegration:
    def test_texplode_registry_discovery(self):
        """ComponentRegistry.discover_all() finds tExplode."""
        ...

    def test_texplode_introspection_schema(self):
        """SchemaIntrospectable generates correct JSON schema."""
        ...

    @pytest.mark.asyncio
    async def test_texplode_in_transform_chain(self, df_with_lists):
        """tExplode used in a MultiQS Transform step via dict config."""
        ...
```

---

## Agent Instructions

When you pick up this task:

1. **Verify TASK-687 is completed** — check `sdd/tasks/completed/TASK-687-texplode-component.md`
2. **Read the implementation** at `querysource/queries/multi/transformations/tExplode.py`
3. **Verify the Codebase Contract** — confirm imports and class signatures
4. **Implement all tests** following the scaffold above
5. **Run tests**: `pytest tests/test_texplode.py -v`
6. **Run lint**: `ruff check tests/test_texplode.py`
7. **Move this file** to `sdd/tasks/completed/TASK-688-texplode-tests.md`
8. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: 
**Date**: 
**Notes**: 

**Deviations from spec**: none | describe if any
