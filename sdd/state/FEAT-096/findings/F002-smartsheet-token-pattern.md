---
id: F002
title: SmartSheetSource — closest analogue (Bearer token + aiohttp)
queries: [Q003]
confidence: high
citations:
  - path: querysource/queries/multi/sources/smartsheet.py
    lines: 1-92
    symbols: [SmartSheetSource, BASE_URL, fetch]
---

# F002 — SmartSheetSource (Bearer-token + aiohttp analogue)

The closest analogue to the planned `AirtableSource`. Pattern (lines 1-92):

- Class-level `BASE_URL = "https://api.smartsheet.com/2.0/sheets/"` constant
- Constructor (lines 42-55) reads `options['credentials']['api_key']` with `'SMARTSHEET_API_KEY'` as default env-var fallback
- `async def fetch()` (lines 57-92):
  - Validates required identifier (`file_id`)
  - Builds `Authorization: Bearer <token>` header
  - Uses raw `aiohttp.ClientSession(timeout=ClientTimeout(total=30))` — NO custom HTTP interface
  - Handles 401 / 429 explicitly with `RuntimeError`
  - Parses bytes (Excel here) → `pd.DataFrame`
  - Returns `df.infer_objects()`

**Implication for AirtableSource:** an identical skeleton works for Airtable record fetch, swapping the Excel parser for Airtable's JSON `records` payload pagination loop. No new HTTP abstraction needed.
