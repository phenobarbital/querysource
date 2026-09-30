---
id: F007
query_id: Q007
type: grep
intent: existing GCS client
executed_at: 2026-09-30T14:27:22Z
parent_id: null
depth: 0
---

# F007 — existing GCS client

## Summary

No module under querysource/ imports google.cloud.storage or gcsfs — there is no GCS client pattern to reuse. Google credentials in conf.py are BIGQUERY_CREDENTIALS (service-account JSON path, default env/google/bigquery.json), BIGQUERY_PROJECT_ID and GOOGLE_CREDENTIALS_FILE.

## Citations

- path: `querysource/conf.py`
  lines: 193-203
  symbol: `BIGQUERY_CREDENTIALS / BIGQUERY_PROJECT_ID`
  excerpt: navconfig, Path-resolved

- path: `querysource/conf.py`
  lines: 300-305
  symbol: `GOOGLE_CREDENTIALS_FILE`
  excerpt: google API creds

