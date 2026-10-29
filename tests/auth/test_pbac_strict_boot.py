"""FEAT-182 TASK-947: QS_PBAC_STRICT_BOOT.

Drives ``QuerySource.setup()`` (lazy, no DB) with the real ``setup_pbac`` over
a policy directory holding invalid YAML, so the bootstrap genuinely fails.
"""
from __future__ import annotations

import logging

import pytest
from aiohttp import web

from querysource.auth import setup_pbac

_GOOD_POLICY = """
version: "1.0"
defaults:
  effect: deny
policies:
  - name: p
    effect: allow
    resources: ["slug:*"]
    actions: ["slug:execute"]
    subjects:
      groups: ["g"]
    priority: 1
"""


@pytest.fixture
def bad_policy_dir(tmp_path):
    (tmp_path / "broken.yaml").write_text("version: [unterminated\n  - : :\n\t{{{")
    # Precondition: the real bootstrap really fails on this directory.
    probe = web.Application()
    assert setup_pbac(probe, policy_dir=str(tmp_path)) == (None, None, None)
    assert probe.get("security") is None
    return str(tmp_path)


def _configure(monkeypatch, policy_dir: str, *, strict: bool) -> None:
    monkeypatch.setattr("querysource.services.QS_PBAC_ENABLED", True)
    monkeypatch.setattr("querysource.services.QS_POLICY_PATH", policy_dir)
    monkeypatch.setattr("querysource.services.QS_PBAC_CACHE_TTL", 60)
    monkeypatch.setattr("querysource.services.QS_PBAC_STRICT_BOOT", strict)


def test_strict_boot_raises(monkeypatch, bad_policy_dir):
    """Enabled + strict + failed bootstrap → RuntimeError from setup()."""
    from querysource.services import QuerySource

    _configure(monkeypatch, bad_policy_dir, strict=True)
    qs = QuerySource(lazy=True)
    with pytest.raises(RuntimeError, match="PBAC bootstrap returned None"):
        qs.setup(web.Application())


def test_non_strict_boot_warns(monkeypatch, bad_policy_dir, caplog):
    """Enabled + not strict + failed bootstrap → warning only (today's behaviour)."""
    from querysource.services import QuerySource

    _configure(monkeypatch, bad_policy_dir, strict=False)
    qs = QuerySource(lazy=True)
    app = web.Application()
    with caplog.at_level(logging.WARNING, logger="querysource.services"):
        qs.setup(app)  # must not raise
    assert any("PBAC bootstrap returned None" in r.getMessage() for r in caplog.records)
    assert app.get("security") is None


def test_strict_boot_ok_with_valid_policies(monkeypatch, tmp_path):
    """Strict does not interfere with a healthy bootstrap."""
    from querysource.services import QuerySource

    (tmp_path / "defaults.yaml").write_text(_GOOD_POLICY)
    _configure(monkeypatch, str(tmp_path), strict=True)
    qs = QuerySource(lazy=True)
    app = web.Application()
    qs.setup(app)
    assert app.get("security") is not None


def test_strict_boot_defaults_false():
    """conf default keeps today's behaviour."""
    from querysource import conf

    assert conf.QS_PBAC_STRICT_BOOT is False
