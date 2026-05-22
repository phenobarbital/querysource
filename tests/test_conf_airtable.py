"""Tests for Airtable env constants in querysource/conf.py (TASK-678)."""
import importlib


def test_constants_importable():
    mod = importlib.import_module("querysource.conf")
    for name in (
        "AIRTABLE_CLIENT_ID",
        "AIRTABLE_CLIENT_SECRET",
        "AIRTABLE_BASE_ID",
        "AIRTABLE_ACCESS_TOKEN",
        "AIRTABLE_REDIRECT_URI",
        "QS_AIRTABLE_OAUTH_ENABLED",
    ):
        assert hasattr(mod, name), f"missing conf attr: {name}"


def test_oauth_disabled_by_default(monkeypatch):
    # navconfig reads from env first; with the var unset, default must be False.
    monkeypatch.delenv("QS_AIRTABLE_OAUTH_ENABLED", raising=False)
    import querysource.conf as conf
    importlib.reload(conf)
    assert conf.QS_AIRTABLE_OAUTH_ENABLED is False


def test_redirect_uri_default_contains_callback_path(monkeypatch):
    monkeypatch.delenv("AIRTABLE_REDIRECT_URI", raising=False)
    import querysource.conf as conf
    importlib.reload(conf)
    assert "/api/v1/qs/integrations/airtable/callback" in conf.AIRTABLE_REDIRECT_URI
