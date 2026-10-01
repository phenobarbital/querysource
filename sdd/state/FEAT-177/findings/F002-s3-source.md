---
id: F002
query_id: Q002
type: read
intent: reference implementation to mirror
executed_at: 2026-09-30T14:27:22Z
parent_id: null
depth: 0
---

# F002 — reference implementation to mirror

## Summary

S3Source downloads ONE object fully into memory with aioboto3 (client.get_object + Body.read), then parses bytes as CSV/CSV.GZ/Excel only (no parquet). Credentials resolved via ThreadSource.resolve_credential; falls back to default AWS chain when names are unresolved. Warns above 100 MB. Does NOT call resolve_masks on file/directory.

## Citations

- path: `querysource/queries/multi/sources/s3.py`
  lines: 24-70
  symbol: `S3Source.__init__`
  excerpt: credentials {region_name,bucket,aws_key,aws_secret} + source {file,directory}

- path: `querysource/queries/multi/sources/s3.py`
  lines: 78-125
  symbol: `S3Source._parse_content`
  excerpt: csv/gz/excel parsing, pd.read_csv/read_excel

- path: `querysource/queries/multi/sources/s3.py`
  lines: 127-165
  symbol: `S3Source.fetch`
  excerpt: lazy import aioboto3; get_object; Body.read; ImportError -> 'pip install querysource[s3]'

