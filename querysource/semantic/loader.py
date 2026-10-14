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
        """Initialize a loader with its model root, cache bound, and file size cap."""
        self.logger = logging.getLogger(__name__)
        self._models_dir = Path(models_dir)
        self._cache_size = cache_size
        self._max_bytes = max_model_bytes
        self._cache: OrderedDict[CacheKey, Any] = OrderedDict()
        self._lock = threading.Lock()
        self._tmpdir: Path | None = None

    def resolve(self, slug: str, revision: str, resource: SemanticResource) -> SemanticModelRef:
        """Resolve a semantic definition into a hashed compiler input reference."""
        definition = resource.semantic.definition
        warnings: list[str] = []
        if isinstance(definition, dict):
            if "path" in definition:
                warnings.append("Inline semantic definition takes precedence over its path key.")
                self.logger.warning("Semantic inline definition takes precedence over path for slug %s", slug)
            document = yaml.safe_dump(definition, sort_keys=True).encode()
            digest = hashlib.sha256(document).hexdigest()
            source_path, resolved_bytes = self._write_inline(definition, resource.semantic.format, digest)
        else:
            source_path = self._guard_path(slug, definition)
            try:
                resolved_bytes = source_path.read_bytes()
            except OSError as exc:
                self.logger.warning("Semantic model file is unreadable for slug %s", slug)
                raise SemanticResourceError(f"Unable to read semantic model for '{slug}'") from exc
            if len(resolved_bytes) > self._max_bytes:
                self.logger.warning("Semantic model file exceeds size cap for slug %s", slug)
                raise SemanticResourceError(f"Semantic model for '{slug}' exceeds the size limit")
            digest = hashlib.sha256(resolved_bytes).hexdigest()

        return SemanticModelRef(
            slug=slug,
            revision=revision,
            format=resource.semantic.format,
            scope=resource.semantic.scope,
            source_path=source_path,
            model_version=f"{revision}:{hashlib.sha256(resolved_bytes).hexdigest()[:12]}",
            warnings=warnings,
        )

    def _guard_path(self, slug: str, relative: str) -> Path:
        """Return an existing path only when its resolved location is inside the model root."""
        models_dir = self._models_dir.resolve()
        candidate = (models_dir / relative).resolve()
        try:
            candidate.relative_to(models_dir)
        except ValueError as exc:
            self.logger.warning("Semantic model path escapes models directory for slug %s", slug)
            raise SemanticResourceError(f"Semantic model path escapes configured directory for '{slug}'") from exc
        if not candidate.is_file():
            self.logger.warning("Semantic model file is missing for slug %s", slug)
            raise SemanticResourceError(f"Semantic model file is missing for '{slug}'")
        return candidate

    def _write_inline(self, document: dict[str, Any], fmt: str, digest: str) -> tuple[Path, bytes]:
        """Serialize an inline document once into the loader's private temporary directory."""
        document_bytes = yaml.safe_dump(document, sort_keys=True).encode()
        if self._tmpdir is None:
            self._tmpdir = Path(tempfile.mkdtemp(prefix="qs-semantic-"))
        source_path = self._tmpdir / f"{digest}{_SUFFIX[fmt]}"
        if not source_path.exists():
            source_path.write_bytes(document_bytes)
        return source_path, document_bytes

    def cached(self, key: CacheKey) -> Any | None:
        """Return a cached layer and mark it as most recently used."""
        with self._lock:
            layer = self._cache.get(key)
            if layer is not None:
                self._cache.move_to_end(key)
            return layer

    def store(self, key: CacheKey, layer: Any) -> None:
        """Store a layer and evict least-recently-used entries past the cache bound."""
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
    """Return the process-wide loader configured by navconfig settings."""
    global _LOADER  # noqa: PLW0603
    if _LOADER is None:
        from ..conf import QS_SEMANTIC_CACHE_SIZE, QS_SEMANTIC_MAX_MODEL_BYTES, QS_SEMANTIC_MODELS_DIR

        _LOADER = ModelLoader(
            Path(QS_SEMANTIC_MODELS_DIR),
            cache_size=QS_SEMANTIC_CACHE_SIZE,
            max_model_bytes=QS_SEMANTIC_MAX_MODEL_BYTES,
        )
        atexit.register(_LOADER.close)
    return _LOADER
