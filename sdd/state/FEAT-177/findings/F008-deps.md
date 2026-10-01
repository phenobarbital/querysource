---
id: F008
query_id: Q010
type: grep+lock
intent: dependency availability
executed_at: 2026-09-30T14:27:22Z
parent_id: null
depth: 0
---

# F008 — dependency availability

## Summary

pyarrow (25.0.1) and google-cloud-storage (3.14.1) are installed but NOT direct querysource dependencies: pyarrow arrives via deltalake/pygwalker/streamlit, google-cloud-storage via asyncdb. fsspec is transitive (autoviz/modin); s3fs/gcsfs are absent. aioboto3 is declared in the `s3` optional extra; polars==1.27.1 is a direct dependency. pandas 3.0.6 in venv.

## Citations

- path: `pyproject.toml`
  lines: 157-160
  symbol: `[project.optional-dependencies].s3`
  excerpt: aioboto3>=12.0

- path: `pyproject.toml`
  lines: 98
  symbol: `polars==1.27.1`
  excerpt: direct dep

- path: `uv.lock`
  lines: -
  symbol: `pyarrow 25.0.1`
  excerpt: transitive: deltalake, pygwalker, streamlit

- path: `uv.lock`
  lines: -
  symbol: `google-cloud-storage 3.14.1`
  excerpt: transitive: asyncdb

