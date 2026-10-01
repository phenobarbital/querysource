---
id: F003
query_id: Q003
type: read
intent: source base contract
executed_at: 2026-09-30T14:27:56Z
parent_id: null
depth: 0
---
# F003 — ThreadSource contract
## Summary
`ThreadSource(threading.Thread, ABC)` requires only `async fetch() -> pd.DataFrame`; `run()` spins a fresh event loop per thread, puts `{name: df}` on the queue, maps DataFrame-empty (DataNotFound/NoDataFound) to info log. Provides `resolve_credential` (ALL_CAPS → navconfig lookup) and `resolve_masks` (fnExecutor masks).
## Citations
- path: `querysource/queries/multi/sources/base.py`
  lines: 14-43
  symbol: `ThreadSource.__init__`
- path: `querysource/queries/multi/sources/base.py`
  lines: 45-70
  symbol: `ThreadSource.resolve_credential`
- path: `querysource/queries/multi/sources/base.py`
  lines: 72-104
  symbol: `ThreadSource.resolve_masks`
- path: `querysource/queries/multi/sources/base.py`
  lines: 116-163
  symbol: `ThreadSource.fetch`, `ThreadSource.run`
