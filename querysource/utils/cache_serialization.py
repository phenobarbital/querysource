"""Helpers for serializing query cache payloads for Redis."""
from __future__ import annotations

from typing import Any

PARQUET_MAGIC = b"PAR1"


def is_parquet_payload(payload: Any) -> bool:
    """Return True when payload looks like a Parquet file payload."""
    if isinstance(payload, memoryview):
        payload = payload.tobytes()
    if not isinstance(payload, (bytes, bytearray)):
        return False
    raw = bytes(payload)
    return len(raw) >= 4 and raw[:4] == PARQUET_MAGIC


def serialize_cache_payload(rows: list[dict[str, Any]]) -> bytes:
    """Serialize query rows into Parquet bytes for Redis storage."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(rows)
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd")
    return sink.getvalue().to_pybytes()


def deserialize_cache_payload(payload: Any) -> list[dict[str, Any]]:
    """Deserialize Parquet bytes from Redis into Python rows."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    if isinstance(payload, memoryview):
        payload = payload.tobytes()
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("Parquet cache payload must be bytes-like")

    reader = pa.BufferReader(bytes(payload))
    table = pq.read_table(reader)
    return table.to_pylist()
