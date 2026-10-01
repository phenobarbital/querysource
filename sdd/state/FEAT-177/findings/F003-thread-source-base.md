---
id: F003
query_id: Q003
type: read
intent: source base contract
executed_at: 2026-09-30T14:27:22Z
parent_id: null
depth: 0
---

# F003 — source base contract

## Summary

ThreadSource is a threading.Thread+ABC; run() creates a fresh event loop per thread and runs fetch(); a non-None DataFrame is put in the queue as {name: df}. DataNotFound/NoDataFound are logged as info (HTTP 204 path). Provides resolve_credential (navconfig lookup for UPPER_SNAKE names) and resolve_masks (flowtask-style masks popped from options).

## Citations

- path: `querysource/queries/multi/sources/base.py`
  lines: 14-43
  symbol: `ThreadSource.__init__`
  excerpt: pops 'masks' from options

- path: `querysource/queries/multi/sources/base.py`
  lines: 45-70
  symbol: `ThreadSource.resolve_credential`
  excerpt: navconfig lookup

- path: `querysource/queries/multi/sources/base.py`
  lines: 72-104
  symbol: `ThreadSource.resolve_masks`
  excerpt: fnExecutor mask resolution

- path: `querysource/queries/multi/sources/base.py`
  lines: 116-130
  symbol: `ThreadSource.fetch`
  excerpt: abstract async -> pd.DataFrame

- path: `querysource/queries/multi/sources/base.py`
  lines: 132-163
  symbol: `ThreadSource.run`
  excerpt: per-thread event loop, DataNotFound handling

