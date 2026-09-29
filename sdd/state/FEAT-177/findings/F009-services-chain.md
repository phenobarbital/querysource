---
id: F009
query_id: Q010
type: tree
intent: confirm HTTP-server import chain (user-named example)
executed_at: 2026-09-29T15:55:00Z
parent_id: F006
depth: 1
---
# F009 — querysource.services pays weasyprint via handlers → queries → outputs → writers.pdf

## Summary
Follow-up to the user's example. `querysource/services.py:22` imports `from .handlers import (...)`; the importtime tree shows `querysource.handlers.describe` (951,709 µs) → `querysource.queries.qs` (913,799) → `querysource.queries.base` (509,178) → `querysource.outputs` (504,600) → `querysource.outputs.output` (504,514) → `querysource.outputs.writers` (504,109) → `querysource.outputs.writers.pdf` (500,823, nearly all weasyprint). This confirms that HTTP server startup loads weasyprint before any PDF request is made.

## Citations
- path: `querysource/services.py`
  lines: 21-22
  excerpt: |
    from .datasources.handlers import DatasourceDrivers, DatasourceView
    from .handlers import (
- path: `querysource/handlers/describe.py`
- path: `querysource/queries/qs.py`
- path: `querysource/queries/base.py`
  lines: 14
- path: `querysource/outputs/writers/pdf.py`
  lines: 7
