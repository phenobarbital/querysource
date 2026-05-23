# TASK-661: Refactor Existing Abstract Classes

**Feature**: FEAT-095 — MultiQuery Documentation System
**Spec**: `sdd/specs/multiquery-documentation.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-660
**Assigned-to**: unassigned

---

## Context

> Implements Module 2 from the spec. Refactors the three existing abstract classes
> (AbstractTransform, AbstractOperator, AbstractComponent) to inherit from the new
> AbstractMulti base class, removing duplicated boilerplate while preserving
> subclass-specific logic. This is the critical backward-compatibility task.

---

## Scope

- Modify `AbstractTransform` to inherit from `AbstractMulti` instead of being a plain class
- Modify `AbstractOperator` to inherit from `AbstractMulti` instead of `ABC` directly
- Modify `AbstractComponent` to inherit from `AbstractMulti` instead of `ABC` directly
- Remove duplicated code from each: `__init__` kwargs loop, `__aenter__`/`__aexit__`, `close()`
- Keep subclass-specific logic:
  - `AbstractTransform`: `self._backend = 'pandas'`, `self.logger`, data validation in `start()`
  - `AbstractOperator`: `self._backend` from kwargs, `self._pd` modin/pandas branch
  - `AbstractComponent`: currently minimal (just `data`), keep as thin wrapper
- Set `_category` class attribute on each: `"Transformations"`, `"Operators"`, `"Components"`
- Rename `AbstractTransform.colum_info()` to `_print_info()` (resolved in spec Q4)
- Remove `_print_info()` from AbstractOperator and AbstractComponent (now inherited from AbstractMulti)
- Write backward-compatibility tests verifying existing concrete subclasses still work

**NOT in scope**: Modifying any concrete operator/transform classes, adding docstrings (TASK-662), registry (TASK-663)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/transformations/abstract.py` | MODIFY | Inherit AbstractMulti, remove duplicated boilerplate |
| `querysource/queries/multi/operators/abstract.py` | MODIFY | Inherit AbstractMulti, remove duplicated boilerplate |
| `querysource/queries/multi/components/abstract.py` | MODIFY | Inherit AbstractMulti, remove duplicated boilerplate |
| `tests/test_abstract_refactor.py` | CREATE | Backward-compatibility tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# The new base class (created by TASK-660)
from querysource.queries.multi.abstract import AbstractMulti  # will exist after TASK-660

# Existing imports used in the abstract classes
from abc import ABC, abstractmethod  # used by AbstractOperator, AbstractComponent
import logging  # used by AbstractTransform
import pandas as pd  # used by all three
from typing import Union  # used by AbstractTransform
from querysource.exceptions import QueryException  # used in __aexit__ of all three
```

### Existing Signatures to Use
```python
# querysource/queries/multi/transformations/abstract.py
class AbstractTransform:  # line 12 — plain class, no ABC
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 13
        self._backend = 'pandas'  # line 14 — KEEP in subclass
        self.data = data  # line 15 — MOVE to AbstractMulti via super()
        self.logger = logging.getLogger(f'QS.Transform.{self.__class__.__name__}')  # line 16 — KEEP in subclass
    def colum_info(self, df):  # line 20 — RENAME to _print_info, or REMOVE (inherited)
    async def start(self):  # line 26 — KEEP (has transform-specific data validation)
    async def __aenter__(self):  # line 45 — REMOVE (inherited from AbstractMulti)
    async def __aexit__(self, ...):  # line 49 — REMOVE (inherited from AbstractMulti)
    async def close(self):  # line 56 — REMOVE (inherited from AbstractMulti)
    @abstractmethod async def run(self):  # line 59 — REMOVE (inherited from AbstractMulti)

# querysource/queries/multi/operators/abstract.py
class AbstractOperator(ABC):  # line 15
    def __init__(self, data: dict, **kwargs) -> None:  # line 20
        self._backend = kwargs.get('backend', 'pandas')  # line 21 — KEEP in subclass
        self._pd = mpd or pd  # lines 24/27 — KEEP in subclass (modin logic)
        self.data = data  # line 28 — MOVE to AbstractMulti via super()
    async def __aenter__(self):  # line 32 — REMOVE
    async def __aexit__(self, ...):  # line 36 — REMOVE
    @abstractmethod async def start(self):  # line 43 — KEEP (abstract in operator)
    @abstractmethod async def run(self):  # line 48 — REMOVE (inherited)
    async def close(self):  # line 53 — REMOVE (inherited)
    def _print_info(self, df):  # line 58 — REMOVE (inherited from AbstractMulti)

# querysource/queries/multi/components/abstract.py
class AbstractComponent(ABC):  # line 15
    def __init__(self, data: dict, **kwargs) -> None:  # line 20
        self.data = data  # line 21 — MOVE to AbstractMulti via super()
    # All methods: REMOVE (inherited from AbstractMulti)

# Concrete subclasses that MUST still work after refactor:
class Join(AbstractOperator):  # operators/Join.py:13
class Concat(AbstractOperator):  # operators/Concat.py:8
class Map(AbstractTransform):  # transformations/Map.py:14
class tPandas(AbstractTransform):  # transformations/tPandas.py:12
```

### Does NOT Exist
- ~~`AbstractTransform(ABC)`~~ — AbstractTransform does NOT inherit ABC currently (that changes via AbstractMulti)
- ~~`AbstractOperator.logger`~~ — AbstractOperator does NOT have a logger attribute
- ~~`AbstractComponent._backend`~~ — AbstractComponent does NOT have a _backend attribute

---

## Implementation Notes

### Refactoring Strategy per Class

**AbstractTransform:**
```python
class AbstractTransform(AbstractMulti):
    _category = "Transformations"

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        self._backend = 'pandas'
        self.logger = logging.getLogger(f'QS.Transform.{self.__class__.__name__}')
        super().__init__(data, **kwargs)

    async def start(self):
        # KEEP existing data validation logic (check non-empty DataFrame)
        ...
```

**AbstractOperator:**
```python
class AbstractOperator(AbstractMulti):
    _category = "Operators"

    def __init__(self, data: dict, **kwargs) -> None:
        self._backend = kwargs.get('backend', 'pandas')
        # modin/pandas branch logic
        try:
            import modin.pandas as mpd
            self._pd = mpd
        except ImportError:
            self._pd = pd
        super().__init__(data, **kwargs)
```

**AbstractComponent:**
```python
class AbstractComponent(AbstractMulti):
    _category = "Components"
    # Minimal — just inherits everything from AbstractMulti
```

### Key Constraints
- `super().__init__` MUST be called AFTER subclass-specific attrs are set (so kwargs setattr in AbstractMulti doesn't overwrite them)
- Existing concrete classes call `super().__init__(data, **kwargs)` — this chain must still work
- The `__aexit__` error handling pattern (raising QueryException) is in AbstractMulti now
- `AbstractOperator.start()` is `@abstractmethod` — keep it that way in AbstractOperator even though AbstractMulti has a default `start()`

---

## Acceptance Criteria

- [ ] `AbstractTransform` inherits from `AbstractMulti`
- [ ] `AbstractOperator` inherits from `AbstractMulti`
- [ ] `AbstractComponent` inherits from `AbstractMulti`
- [ ] No duplicated `__aenter__`/`__aexit__`/`close()` code in any of the three
- [ ] `colum_info()` renamed to `_print_info()` on AbstractTransform (or removed if inherited)
- [ ] `_category` set correctly on each: `"Transformations"`, `"Operators"`, `"Components"`
- [ ] `Join(data={}).get_schema()` works (inherited from AbstractMulti)
- [ ] Existing concrete subclasses (Join, Concat, Map, tPandas, etc.) still instantiate correctly
- [ ] Tests pass: `pytest tests/test_abstract_refactor.py -v`
- [ ] No import errors in existing code

---

## Test Specification

```python
# tests/test_abstract_refactor.py
import pytest
from querysource.queries.multi.abstract import AbstractMulti
from querysource.queries.multi.transformations.abstract import AbstractTransform
from querysource.queries.multi.operators.abstract import AbstractOperator
from querysource.queries.multi.components.abstract import AbstractComponent


class TestInheritance:
    def test_transform_inherits_abstract_multi(self):
        assert issubclass(AbstractTransform, AbstractMulti)

    def test_operator_inherits_abstract_multi(self):
        assert issubclass(AbstractOperator, AbstractMulti)

    def test_component_inherits_abstract_multi(self):
        assert issubclass(AbstractComponent, AbstractMulti)


class TestCategory:
    def test_transform_category(self):
        assert AbstractTransform._category == "Transformations"

    def test_operator_category(self):
        assert AbstractOperator._category == "Operators"

    def test_component_category(self):
        assert AbstractComponent._category == "Components"


class TestBackwardCompat:
    def test_concat_instantiates(self):
        from querysource.queries.multi.operators.Concat import Concat
        op = Concat(data={"a": [1, 2], "b": [3, 4]})
        assert op.data is not None

    def test_map_instantiates(self):
        from querysource.queries.multi.transformations.Map import Map
        t = Map(data={"a": [1]}, fields={"x": "y"})
        assert t.data is not None

    def test_introspection_available(self):
        from querysource.queries.multi.operators.Concat import Concat
        schema = Concat.get_schema()
        assert "json_schema" in schema
        assert "attributes" in schema
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-documentation.spec.md` for full context
2. **Check dependencies** — verify TASK-660 is in `sdd/tasks/completed/`
3. **Verify the Codebase Contract** — read the three abstract files to confirm signatures haven't changed
4. **Implement** the refactoring carefully — backward compatibility is critical
5. **Run tests**: `source .venv/bin/activate && pytest tests/test_abstract_refactor.py -v`
6. **Also run existing tests** to verify nothing broke: `pytest tests/ -v --timeout=30 -x`
7. **Move this file** to `sdd/tasks/completed/TASK-661-refactor-abstract-classes.md`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: sdd-worker (Claude)
**Date**: 2026-05-20
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none
