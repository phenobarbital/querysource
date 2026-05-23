---
id: F007
title: No prior Airtable code anywhere in the repository
queries: [Q017]
confidence: high
citations:
  - path: <repo-root>
    lines: grep result
---

# F007 — No prior Airtable references in code

`grep -rln "airtable\|Airtable\|AIRTABLE" .` (excluding .venv/.git/.pyc) returns ONLY:
- proposal/spec documents in `sdd/` (irrelevant — content of other features)
- this feature's own state files

There is **zero** Airtable code, zero existing client, zero existing dependency on `pyairtable` or any Airtable SDK.

**Implication:** AirtableSource is entirely greenfield. We can pick the cleanest implementation (raw aiohttp vs. an SDK like `pyairtable`) without back-compat concerns.
