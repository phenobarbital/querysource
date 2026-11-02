"""Tests for semantic model resolution and cache lifetime."""
from __future__ import annotations

import logging

import pytest

from querysource.semantic.exceptions import SemanticResourceError
from querysource.semantic.loader import ModelLoader
from querysource.semantic.models import SemanticResource


def _res(definition: str | dict[str, object]) -> SemanticResource:
    """Build a minimal sidemantic resource for loader tests."""
    return SemanticResource.model_validate(
        {
            "name": "s",
            "kind": "semantic",
            "semantic": {"format": "sidemantic", "definition": definition},
        }
    )


def test_loader_rejects_path_traversal(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """Reject paths that resolve outside the configured model directory."""
    with caplog.at_level(logging.WARNING):
        with pytest.raises(SemanticResourceError):
            ModelLoader(tmp_path, cache_size=2).resolve("s", "r1", _res("../../etc/passwd"))
    assert "slug s" in caplog.text


def test_loader_model_version_and_cache_key(tmp_path) -> None:
    """Hash contents into the version independently from the database revision."""
    source = tmp_path / "m.yml"
    source.write_text("models: []\n")
    loader = ModelLoader(tmp_path, cache_size=2)
    first = loader.resolve("s", "r1", _res("m.yml"))
    assert first.model_version == loader.resolve("s", "r1", _res("m.yml")).model_version
    source.write_text("models:\n  - name: changed\n")
    changed = loader.resolve("s", "r1", _res("m.yml"))
    assert changed.model_version != first.model_version
    assert loader.resolve("s", "r2", _res("m.yml")).model_version != changed.model_version


def test_loader_inline_wins_and_warns(tmp_path) -> None:
    """Treat a mapping carrying a path key as inline rather than resolving that path."""
    loader = ModelLoader(tmp_path, cache_size=2)
    resolved = loader.resolve("s", "r1", _res({"path": "missing.yml", "models": []}))
    assert resolved.source_path.is_file()
    assert resolved.warnings == ["Inline semantic definition takes precedence over its path key."]
    loader.close()
    assert not resolved.source_path.parent.exists()


def test_loader_cache_lru(tmp_path) -> None:
    """Evict the least recently used cache entry beyond the configured bound."""
    loader = ModelLoader(tmp_path, cache_size=2)
    first = ("r1", "postgres", "ossie", None)
    second = ("r2", "postgres", "ossie", None)
    third = ("r3", "postgres", "ossie", None)
    loader.store(first, object())
    loader.store(second, object())
    loader.store(third, object())
    assert loader.cached(first) is None
    assert loader.cached(second) is not None
    assert loader.cached(third) is not None


def test_loader_model_version_follows_file_edit_with_same_revision(tmp_path) -> None:
    """A path-backed file edit changes model_version (and so the cache key) without a new revision."""
    path = tmp_path / "m.yml"
    path.write_text("models: []\n")
    loader = ModelLoader(tmp_path, cache_size=2)
    first = loader.resolve("s", "r1", _res("m.yml"))
    path.write_text("models: [] # edited\n")
    second = loader.resolve("s", "r1", _res("m.yml"))
    assert first.model_version != second.model_version
    assert first.source_path != second.source_path
    # The compiled copy is immutable: editing the source never changes what was hashed.
    assert first.source_path.read_text() == "models: []\n"
    loader.close()
