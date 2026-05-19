---
id: F001
query: Locate and analyze AbstractTransform, AbstractOperator, AbstractComponent
type: read
---

## Findings

Three parallel abstract base classes exist with near-identical boilerplate:

### AbstractTransform
- **File:** `querysource/queries/multi/transformations/abstract.py`
- Does NOT inherit from ABC (inconsistency)
- Attrs: `_backend`, `data`, `logger`
- Methods: `__aenter__`→`start()`, `__aexit__`→`close()`, `run()` (@abstractmethod)
- Debug helper: `colum_info(df)` (note: typo "colum")

### AbstractOperator
- **File:** `querysource/queries/multi/operators/abstract.py`
- Inherits from ABC
- Attrs: `_backend`, `_pd` (modin/pandas), `data`
- Methods: `__aenter__`→`start()`, `__aexit__`→`close()`, `start()` (@abstractmethod), `run()` (@abstractmethod)
- Debug helper: `_print_info(df)`

### AbstractComponent
- **File:** `querysource/queries/multi/components/abstract.py`
- Inherits from ABC
- Attrs: `data`
- Methods: identical to AbstractOperator
- Debug helper: `_print_info(df)`
- **No concrete implementations exist.** `components/__init__.py` is empty.

### Common Pattern
All three share:
- `__init__(self, data, **kwargs)` with `setattr` loop
- async context manager (`__aenter__`/`__aexit__`)
- `start()`/`run()`/`close()` lifecycle
- Zero classmethods, zero introspection, zero documentation methods

### No Unified Base
No `AbstractMulti` or similar exists anywhere in the codebase.
