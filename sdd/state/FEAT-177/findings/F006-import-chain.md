---
id: F006
query_id: Q007
type: grep
intent: importers / consumers of writers
executed_at: 2026-09-29T15:47:30Z
parent_id: F005
depth: 1
---
# F006 — library path pulls writers via queries/base.py → outputs.dt → outputs/__init__ → output.py

## Summary
`querysource/queries/base.py:14` does `from ..outputs.dt import OutputFactory`. Importing any subpackage runs `querysource/outputs/__init__.py`, which imports `DataOutput` from `.output`, which imports all writers (F002). So even pure-library use of `QS` (no HTTP, no PDF) loads weasyprint: `import querysource.outputs.dt` alone = 1,376,809 µs, of which weasyprint 404,618 and `querysource.outputs.output` 1,376,137. HTTP handlers `querysource/handlers/{service,multi,qsurl}.py` import `DataOutput` from `..outputs` at module top. No non-output code references `WRITERS` or writer classes directly.

## Citations
- path: `querysource/queries/base.py`
  lines: 14
  symbol: `OutputFactory`
  excerpt: |
    from ..outputs.dt import OutputFactory
- path: `querysource/outputs/__init__.py`
  lines: 1-6
  symbol: `DataOutput`
  excerpt: |
    from .output import DataOutput
    __all__ = ('DataOutput', )
- path: `querysource/outputs/dt/__init__.py`
  lines: 1
  excerpt: |
    from .factory import OutputFactory
- path: `querysource/handlers/service.py`
  lines: 24
- path: `querysource/handlers/multi.py`
  lines: 19
- path: `querysource/handlers/qsurl.py`
  lines: 12
- path: `querysource/queries/multi/destinations/__init__.py`
  lines: 37
  symbol: `AbstractDestination`
  excerpt: |
    from querysource.outputs.destinations.abstract import AbstractDestination  # noqa: PLC0415
  note: existing function-local lazy-import precedent
