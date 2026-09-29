---
id: F007
query_id: Q007
type: grep
intent: tests depending on writer import surface
executed_at: 2026-09-29T15:47:40Z
parent_id: null
depth: 0
---
# F007 — tests rely on WRITERS being a mutable mapping and on direct submodule imports

## Summary
`tests/qsurl/test_error_envelope.py:59` does `monkeypatch.setitem(output_module.WRITERS, "json", _StubWriter)` — the registry must remain a module-level mutable mapping whose values may be classes. `tests/unit/test_csv_writer_dataframe.py:12-13` imports `querysource.outputs.writers.csv` / `.tsv` directly. `tests/integration/test_csv_stream_response.py:7` and `tests/tenants/test_tenant_observability_outputs.py:102` import `DataOutput` from `querysource.outputs.output`.

## Citations
- path: `tests/qsurl/test_error_envelope.py`
  lines: 9, 59
  symbol: `WRITERS`
  excerpt: |
    import querysource.outputs.output as output_module
    monkeypatch.setitem(output_module.WRITERS, "json", _StubWriter)
- path: `tests/unit/test_csv_writer_dataframe.py`
  lines: 12-13
- path: `tests/integration/test_csv_stream_response.py`
  lines: 7
- path: `tests/tenants/test_tenant_observability_outputs.py`
  lines: 102
