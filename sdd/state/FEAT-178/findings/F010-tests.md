---
id: F010
query_id: Q010
type: glob
intent: test patterns
executed_at: 2026-09-30T14:27:56Z
parent_id: null
depth: 0
---
# F010 — SharePoint test patterns
## Summary
`tests/test_source_sharepoint.py` (103 lines, TASK-647) is offline: config parsing, navconfig-default fallback, CSV/Excel parsing via `_parse_file_content`, ImportError when msgraph missing (patch.dict sys.modules). Registry membership is asserted in `tests/test_source_registry.py` and `tests/multi/sources/test_registry.py`. No live Graph calls or mocked Graph client.
## Citations
- path: `tests/test_source_sharepoint.py`
  lines: 12-103
  symbol: `TestSharepointSource`
- path: `tests/test_source_registry.py`
  lines: 11-72
- path: `tests/multi/sources/test_registry.py`
  lines: 5-19
