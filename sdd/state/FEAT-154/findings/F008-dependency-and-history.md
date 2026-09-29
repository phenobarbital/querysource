---
id: F008
query_id: Q008
type: grep
intent: dependency declaration and history
executed_at: 2026-09-29T15:48:00Z
parent_id: null
depth: 0
---
# F008 — WeasyPrint is a hard (non-optional) dependency; writer code untouched since 2025-04

## Summary
`pyproject.toml:111` lists `"WeasyPrint>=65.0"` in `[project].dependencies` (not in `[project.optional-dependencies]`, L122+); lock resolves weasyprint 70.0. bokeh/plotly/seaborn/matplotlib/pygal are also hard deps (L87-91). Last commits on `writers/pdf.py` / `writers/__init__.py`: 5fcbba87 2025-04-02 "xml and pdf fixed outputs", a492560a 2025-02-12, fceb1ed6 2025-02-11. `output.py` was touched recently (b57200c TASK-774, 24e5940 TASK-731) in the `DataOutput` body, not the registry.

## Citations
- path: `pyproject.toml`
  lines: 87-91, 111, 122
- path: `querysource/outputs/writers/pdf.py`
- path: `querysource/outputs/output.py`
