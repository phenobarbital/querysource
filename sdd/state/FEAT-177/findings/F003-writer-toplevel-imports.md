---
id: F003
query_id: Q004
type: grep
intent: top-level heavy imports per writer
executed_at: 2026-09-29T15:46:20Z
parent_id: null
depth: 0
---
# F003 — only pdf.py has a heavy top-level import; others already defer heavy libs

## Summary
All writers except `pdf.py` import only stdlib/aiohttp/`.abstract` at top level (csv/tsv add `aiocsv`). Heavy libraries are already imported inside methods: pandas (excel L47, html L26, json L15), bokeh (bokeh L37-41), plotly (plotly L79-80), seaborn/matplotlib/pygal (report L11-13, L142, L255). `pdf.py` L7 does `from weasyprint import HTML` at module level. Disabled writers `describe.py` (great_expectations L15-16) and `eda.py` (sweetviz L5) also import heavily at top level but are not imported by `__init__`.

## Citations
- path: `querysource/outputs/writers/pdf.py`
  lines: 7
  symbol: `HTML`
  excerpt: |
    from weasyprint import HTML
- path: `querysource/outputs/writers/report.py`
  lines: 10-13
  symbol: `render_seaborn`
  excerpt: |
    def render_seaborn(data, chart_type, info: dict):
        from pandas import DataFrame
        import seaborn as sns
- path: `querysource/outputs/writers/bokeh.py`
  lines: 37-41
- path: `querysource/outputs/writers/plotly.py`
  lines: 79-80
- path: `querysource/outputs/writers/describe.py`
  lines: 15-16
- path: `querysource/outputs/writers/eda.py`
  lines: 5
