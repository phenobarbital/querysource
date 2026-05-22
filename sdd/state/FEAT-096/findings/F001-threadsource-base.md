---
id: F001
title: ThreadSource base class — constructor, resolve_credential helper, fetch contract
queries: [Q001]
confidence: high
citations:
  - path: querysource/queries/multi/sources/base.py
    lines: 11-116
    symbols: [ThreadSource, resolve_credential, fetch, run]
---

# F001 — ThreadSource base class

`querysource/queries/multi/sources/base.py:11-116` defines `ThreadSource(threading.Thread, ABC)` — the abstract base every multi-query source extends.

## Constructor signature

```python
def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None
```

- `name`: source instance name (also default `slug`)
- `options`: full per-source config dict (typically with `credentials` + `source` subkeys)
- `request`: the live aiohttp `web.Request` — **already plumbed into every source**, ready to be used for session lookup (line 26)
- `queue`: shared `asyncio.Queue` where `fetch()` results are put under key `{name: df}` (line 107)

## Credential resolver helper

`resolve_credential(key, value)` at lines 37-62 — if `value` looks like an env-var name (uppercase + underscore), it tries `navconfig.config.get(value)`; otherwise returns the literal. This is the canonical pattern every existing source uses (smartsheet, sharepoint, s3).

## Contract for subclasses

- `async def fetch(self) -> pd.DataFrame` (abstract, line 74-88)
- May raise; exceptions are captured in `self.exc` by `run()` (line 108-110)
- `run()` creates a fresh asyncio event loop, calls `fetch()`, and puts the result on the queue (lines 90-116) — subclasses don't deal with the loop or queue plumbing
