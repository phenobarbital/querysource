---
id: F005
query_id: Q006
type: tree
intent: measure import cost of writers and entrypoints
executed_at: 2026-09-29T15:47:00Z
parent_id: null
depth: 0
---
# F005 — weasyprint costs ~440-610 ms, ~27% of QS import; all other writers < 3 ms

## Summary
`python -X importtime` (local .venv, weasyprint 70.0). Cumulative µs:
- `import querysource.outputs.writers`: 1,771,733 total; `querysource.outputs.writers.pdf` 611,393 (weasyprint 611,200; `weasyprint.text.ffi` self 315,508 — cffi/pango load). Every other writer module: 166–2,544 µs.
- `import querysource.queries.qs` (3 runs): total 1,616k–1,660k; weasyprint 437k–451k (~27%).
- `import querysource.services`: 1,962k total, weasyprint 500k.
- `import querysource.handlers.service`: 1,792k total, weasyprint 450k.
- Importing weasyprint also emits 3 `weasyprint.progress` INFO log lines ("Fetching and parsing CSS") at startup.
Other large contributors not caused by writers: `querysource.interfaces.queries` → asyncdb/google.cloud.bigquery (~500k), pandas (~200k). Top-level `import querysource` alone: 242 µs (lazy).

## Citations
- path: `querysource/outputs/writers/pdf.py`
  lines: 7
  symbol: `HTML`
- path: `querysource/outputs/output.py`
  lines: 19-37
- path: `querysource/queries/qs.py`
  symbol: `QS`
- path: `querysource/services.py`
- path: `querysource/handlers/service.py`
