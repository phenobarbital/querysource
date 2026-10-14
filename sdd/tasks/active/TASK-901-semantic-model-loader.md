# TASK-901: Semantic model loader

**Feature**: FEAT-175 — Querysource Semantic Layer (Ossie models compiled by Sidemantic)
**Spec**: `sdd/specs/semantic-layer.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-897, TASK-899
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 3, §7 gotchas (temp files, path traversal, size cap S9). The loader resolves a
resource's definition (inline mapping or path under `QS_SEMANTIC_MODELS_DIR`), hashes the resolved
bytes into `model_version` (S3, AC-6), writes inline documents to a private temp file (AC-8) and
keeps a bounded cache of compiled layers keyed by `(revision, dialect, format, scope)`. It never
imports sidemantic and never opens a warehouse connection.

---

## Scope

- Implement `ModelLoader` (`resolve`, `cached`, `store`, `close`) and a process-wide `get_loader()` factory reading settings.
- Tests `tests/semantic/test_loader.py`.

**NOT in scope**: building the Sidemantic layer (TASK-903).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/semantic/loader.py` | CREATE | `ModelLoader` |
| `tests/semantic/test_loader.py` | CREATE | loader tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.semantic.models import SemanticModelRef, SemanticResource   # TASK-897
from querysource.semantic.exceptions import SemanticResourceError           # TASK-897
from querysource.conf import (QS_SEMANTIC_MODELS_DIR, QS_SEMANTIC_CACHE_SIZE,
                              QS_SEMANTIC_MAX_MODEL_BYTES)                   # TASK-899
import yaml                                                                  # pyyaml (transitive)
```

### Existing Signatures to Use
```python
# querysource/semantic/models.py (TASK-897)
class SemanticModelRef(BaseModel):
    slug: str; revision: str; format: str; scope: str | None; source_path: Path
    model_version: str; warnings: list[str]
class SemanticResource(BaseModel):  # .semantic.definition: str | dict ; .semantic.format ; .semantic.scope
```

### Does NOT Exist
- ~~`sidemantic` import in the loader~~ — forbidden; the loader is compiler-agnostic.
- ~~A "both inline and path" field~~ — `definition` is a single `str | dict`. "Inline wins with a warning" (AC-7) applies when the inline mapping itself carries a path key: FILL IN the exact rule from the brainstorm (`sdd/proposals/semantic-layer.brainstorm.md`) and document it in the Completion Note.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/semantic/loader.py", "action": "CREATE"},
    {"path": "tests/semantic/test_loader.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- `model_version = f"{revision}:{sha256(resolved_bytes).hexdigest()[:12]}"`.
- Path: `(models_dir / definition).resolve()` must be under `models_dir.resolve()`; else `SemanticResourceError` and `logger.warning` with slug.
- Path-backed file size > `max_model_bytes` → `SemanticResourceError`.
- Inline: serialize with `yaml.safe_dump`, write once per `model_version` under `tempfile.mkdtemp(prefix="qs-semantic-")`, suffix `.ossie.yaml` (ossie) or `.yml` (sidemantic) — the suffix drives Sidemantic's format detection.
- Cache: bounded `OrderedDict` LRU of size `cache_size`; thread-safe with a `threading.Lock` (loaders may be used from executor threads).
- `close()` removes the private temp dir (`shutil.rmtree(..., ignore_errors=True)`); register it with `atexit` in `get_loader()`.

---

## Implementation Blueprint

### Steps (in order)
1. Write `ModelLoader` with the hashing/guard helpers — *why*: AC-6/AC-7.
2. Add `get_loader()` singleton reading `conf` — *why*: one process-wide cache (spec M3).
3. Write tests using `tmp_path` as `models_dir`.

### `querysource/semantic/loader.py` (CREATE)
```python
"""Semantic model loader (FEAT-175 M3): resolve, guard, hash, cache. Never imports a compiler."""
from __future__ import annotations

import atexit
import hashlib
import logging
import shutil
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import yaml

from .exceptions import SemanticResourceError
from .models import SemanticModelRef, SemanticResource

CacheKey = tuple[str, str, str, str | None]
_SUFFIX = {"ossie": ".ossie.yaml", "sidemantic": ".yml"}


class ModelLoader:
    """Resolves semantic documents; one process-wide instance (bounded LRU)."""

    def __init__(self, models_dir: Path, *, cache_size: int, max_model_bytes: int = 2 * 1024 * 1024) -> None:
        self.logger = logging.getLogger(__name__)
        self._models_dir = Path(models_dir)
        self._cache_size = cache_size
        self._max_bytes = max_model_bytes
        self._cache: OrderedDict[CacheKey, Any] = OrderedDict()
        self._lock = threading.Lock()
        self._tmpdir: Path | None = None

    def resolve(self, slug: str, revision: str, resource: SemanticResource) -> SemanticModelRef:
        """Inline wins over path (warning). Path guarded under models_dir; size-capped."""
        # FILL IN: branch on isinstance(definition, dict) → _write_inline; str → _guard_path + read_bytes
        # (size cap); compute model_version; return SemanticModelRef(... warnings=[...]) — bounded by AC-6, AC-7

    def _guard_path(self, slug: str, relative: str) -> Path:
        # FILL IN: realpath containment; log + SemanticResourceError on escape/missing — bounded by S9

    def _write_inline(self, document: dict[str, Any], fmt: str, digest: str) -> tuple[Path, bytes]:
        # FILL IN: lazily mkdtemp(prefix="qs-semantic-"); file name f"{digest}{_SUFFIX[fmt]}"; reuse if exists

    def cached(self, key: CacheKey) -> Any | None:
        with self._lock:
            layer = self._cache.get(key)
            if layer is not None:
                self._cache.move_to_end(key)
            return layer

    def store(self, key: CacheKey, layer: Any) -> None:
        with self._lock:
            self._cache[key] = layer
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

    def close(self) -> None:
        """Drop the cache and remove the private temp directory."""
        with self._lock:
            self._cache.clear()
        if self._tmpdir is not None:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None


_LOADER: ModelLoader | None = None


def get_loader() -> ModelLoader:
    """Process-wide loader built from navconfig settings."""
    global _LOADER  # noqa: PLW0603
    if _LOADER is None:
        from ..conf import QS_SEMANTIC_CACHE_SIZE, QS_SEMANTIC_MAX_MODEL_BYTES, QS_SEMANTIC_MODELS_DIR
        _LOADER = ModelLoader(Path(QS_SEMANTIC_MODELS_DIR), cache_size=QS_SEMANTIC_CACHE_SIZE,
                              max_model_bytes=QS_SEMANTIC_MAX_MODEL_BYTES)
        atexit.register(_LOADER.close)
    return _LOADER
```
**Why**: `ModelLoader.__init__` gains `max_model_bytes` (S9) as a keyword with default, so the spec signature still holds.

### FILL IN checklist
- [ ] `resolve` — both branches, model_version; AC-6/AC-7
- [ ] `_guard_path` — traversal + symlink escape; S9
- [ ] `_write_inline` — private temp file; AC-8
- [ ] Test bodies

---

## Acceptance Criteria

- [ ] `../../etc/passwd` → `SemanticResourceError` and a log record (AC-7).
- [ ] Same document + revision → same `model_version`; changed bytes or revision → different (AC-6, AC-23).
- [ ] Cache evicts beyond `cache_size`; `close()` deletes the temp dir.

---

## Validation Commands
- `pytest tests/semantic/test_loader.py -q`

---

## Test Specification

```python
# tests/semantic/test_loader.py
import pytest
from querysource.semantic.loader import ModelLoader
from querysource.semantic.models import SemanticResource
from querysource.semantic.exceptions import SemanticResourceError


def _res(definition):
    return SemanticResource.model_validate({"name": "s", "kind": "semantic",
                                            "semantic": {"format": "sidemantic", "definition": definition}})


def test_loader_rejects_path_traversal(tmp_path, caplog):
    with pytest.raises(SemanticResourceError):
        ModelLoader(tmp_path, cache_size=2).resolve("s", "r1", _res("../../etc/passwd"))


def test_loader_model_version_and_cache_key(tmp_path):
    # FILL IN: write tmp_path/"m.yml"; resolve twice → equal model_version; edit file → differs; r2 → differs


def test_loader_inline_wins_and_warns(tmp_path):
    # FILL IN: per the rule recorded in Does NOT Exist


def test_loader_cache_lru(tmp_path):
    # FILL IN: store 3 keys with cache_size=2 → first evicted
```

---

## Agent Instructions

1. Work in the feature worktree; TASK-897 and TASK-899 must be done.
2. Implement; run validation; commit only listed files.
3. Close with `scripts/sdd/close_task.sh TASK-901 semantic-layer verified`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
