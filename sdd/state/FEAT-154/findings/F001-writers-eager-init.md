---
id: F001
query_id: Q002
type: read
intent: eager import list
executed_at: 2026-09-29T15:46:00Z
parent_id: null
depth: 0
---
# F001 — writers/__init__.py eagerly imports every writer module

## Summary
`querysource/outputs/writers/__init__.py` imports 13 writer classes at module load (json, txt, csv, excel, html, bokeh, plotly, tsv, report, pickle, table, pdf, xml); profiling/eda/describe/clustering are commented out.

## Citations
- path: `querysource/outputs/writers/__init__.py`
  lines: 1-17
  symbol: `PDFWriter`
  excerpt: |
    from .json import jsonWriter
    ...
    from .pdf import PDFWriter
    # from .profiling import ProfileWriter
    from .xml import XMLWriter
