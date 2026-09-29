---
id: F002
query_id: Q003
type: read
intent: WRITERS registry and dispatch
executed_at: 2026-09-29T15:46:10Z
parent_id: null
depth: 0
---
# F002 — output.py builds a static WRITERS dict of classes; dispatch is by format key

## Summary
`querysource/outputs/output.py` imports all writer classes from `.writers` at module top (L19-37) and builds `WRITERS` (L39-62) mapping 18 format keys (json, table, txt, plain, csv, tsv, excel/xls/xlsx/xlsm/ods, html, bokeh, plotly, pickle, report, pdf, xml) to classes. `DataOutput.response()` looks up `WRITERS[self.format]`, falls back to `WRITERS['json']` on KeyError, then instantiates the class (L217-234). The class is only needed at response time.

## Citations
- path: `querysource/outputs/output.py`
  lines: 19-62
  symbol: `WRITERS`
  excerpt: |
    from .writers import (BokehWriter, CSVWriter, ..., PDFWriter, ...)
    WRITERS = {"json": jsonWriter, ..., 'pdf': PDFWriter, 'xml': XMLWriter}
- path: `querysource/outputs/output.py`
  lines: 211-234
  symbol: `DataOutput.response`
  excerpt: |
    try:
        wt = WRITERS[self.format]
    except KeyError:
        wt = WRITERS['json']
    writer = wt(request=self.request, resultset=self.query, ...)
