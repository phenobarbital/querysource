---
id: F013
query_id: Q012
type: read
intent: other places in the codebase that define a partial-matching vocabulary (naming precedent)
executed_at: 2026-10-07T00:17:00Z
duration_ms: 200
parent_id: F010
depth: 1
---

# F013 — In-memory DataFrame filters already use `startswith`/`endswith`/`contains`/`regex` names and raise `QueryException` on invalid expressions

## Summary

`querysource/types/dt/filters.py` (pandas post-filtering) accepts expressions
`contains`, `not_contains`, `startswith`, `not_startswith`, `endswith`, `not_endswith`,
`regex`, `not_regex`, `fullmatch`, and raises `QueryException("Invalid expression: ...")`
for unknown ones. The naming the request asks for (`startswith`, `endswith`, `contains`,
`regex`) is therefore already the project's vocabulary; a `not_*` family exists too.

## Citations

- path: `querysource/types/dt/filters.py`
  lines: 72-78
  symbol: regex / not_regex / fullmatch branch
- path: `querysource/types/dt/filters.py`
  lines: 80-92
  symbol: contains / startswith / endswith branch
  excerpt: |
    if expression == 'contains':
        return f"df['{column}'].str.contains(r'{value}', na=False, case=False)"
    elif expression == 'startswith':
        return f"df['{column}'].str.startswith('{value}')"
- path: `querysource/types/dt/filters.py`
  lines: 105-108
  symbol: `raise QueryException(f"Invalid expression: {expression}")`
