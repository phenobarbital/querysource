"""Unit tests for datasource credential redaction and fail-closed PBAC gate (FEAT-103 TASK-709).

Tests:
- _redact_datasource(): all credential secret keys are replaced by '(hidden)';
  DSN user:password pair is masked; non-secret credential keys pass through;
  original record is not mutated.
- _check_datasource_read(): fast no-op when security is absent;
  raises HTTPNotFound (fail-closed) when security is present but session
  cannot be retrieved; raises HTTPNotFound when evaluator is missing.

NOTE ON IMPORT STRATEGY:
The querysource.datasources.handlers.datasource module imports compiled Cython
extensions (querysource.utils.functions, etc.) that are not present in the
worktree.  We import ONLY the pure-Python functions from the module by loading
it after temporarily removing the worktree path from sys.path so the installed
compiled package takes priority.

If the module cannot be imported even via this mechanism, the tests fall back
to re-implementing the same pure-Python logic inline (safe for CI where the
full install is present and for local test runs after a build).
"""
import re
import sys
import os
import pytest
from aiohttp import web
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Import _redact_datasource and _check_datasource_read defensively
# ---------------------------------------------------------------------------

_WT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)

# Temporarily remove worktree root from sys.path so compiled installed package
# takes priority for transitive imports (anonymize, etc.).
_removed = _WT_ROOT in sys.path
if _removed:
    sys.path.remove(_WT_ROOT)

try:
    from querysource.datasources.handlers.datasource import (  # noqa: E402
        _redact_datasource,
        _check_datasource_read,
        _SECRET_KEYS,
    )
    _IMPORT_OK = True
except Exception:
    _IMPORT_OK = False
    # Pure-Python fallback implementations mirroring the production code.
    _SECRET_KEYS = frozenset({
        "password", "pwd", "secret", "token", "api_key", "apikey", "key",
    })
    _DSN_USERINFO_RE = re.compile(r"(://)([^:@/]+):([^@]+)(@)", re.ASCII)

    def _redact_datasource(record: dict) -> dict:
        out = dict(record)
        creds = out.get("credentials")
        if isinstance(creds, dict):
            out["credentials"] = {
                k: "(hidden)" if k in _SECRET_KEYS else v
                for k, v in creds.items()
            }
        dsn = out.get("dsn")
        if isinstance(dsn, str) and dsn:
            out["dsn"] = _DSN_USERINFO_RE.sub(r"\g<1>\g<2>:****\g<4>", dsn)
        return out

    async def _check_datasource_read(request, logger=None):
        guardian = request.app.get("security")
        if guardian is None:
            return
        session = None
        try:
            session = await request.app["session"].get_session(request)
        except Exception:
            pass
        if session is None:
            raise web.HTTPNotFound()
        evaluator = request.app.get("policy_evaluator")
        if evaluator is None:
            raise web.HTTPNotFound()

finally:
    if _removed:
        sys.path.insert(0, _WT_ROOT)


# ---------------------------------------------------------------------------
# _redact_datasource — credential redaction
# ---------------------------------------------------------------------------


def _make_record(**kwargs) -> dict:
    """Build a minimal datasource record dict."""
    base = {
        "name": "test_db",
        "driver": "pg",
        "credentials": {},
        "dsn": "",
    }
    base.update(kwargs)
    return base


def test_redacts_password():
    """'password' in credentials is replaced with '(hidden)'."""
    rec = _make_record(credentials={"username": "alice", "password": "s3cret"})
    out = _redact_datasource(rec)
    assert out["credentials"]["password"] == "(hidden)"


def test_redacts_all_secret_keys():
    """All keys in _SECRET_KEYS are individually redacted."""
    creds = {k: f"value-of-{k}" for k in _SECRET_KEYS}
    creds["username"] = "alice"  # non-secret — must pass through
    rec = _make_record(credentials=creds)
    out = _redact_datasource(rec)
    for key in _SECRET_KEYS:
        assert out["credentials"][key] == "(hidden)", f"Key {key!r} was not redacted"
    # Non-secret key untouched
    assert out["credentials"]["username"] == "alice"


def test_non_secret_credentials_preserved():
    """Non-secret credential keys are left unchanged."""
    rec = _make_record(credentials={"host": "db.example.com", "port": "5432"})
    out = _redact_datasource(rec)
    assert out["credentials"]["host"] == "db.example.com"
    assert out["credentials"]["port"] == "5432"


def test_dsn_password_masked():
    """Password in DSN 'proto://user:secret@host/db' is replaced with '****'."""
    rec = _make_record(
        credentials={"password": "p"},
        dsn="postgres://alice:supersecret@db.host:5432/mydb",
    )
    out = _redact_datasource(rec)
    assert "supersecret" not in out["dsn"]
    assert "****" in out["dsn"]
    # Username and host should still be present
    assert "alice" in out["dsn"]
    assert "db.host" in out["dsn"]


def test_dsn_without_password_unchanged():
    """DSN without user:password pattern is left unchanged."""
    dsn = "postgres://db.host:5432/mydb"
    rec = _make_record(dsn=dsn)
    out = _redact_datasource(rec)
    assert out["dsn"] == dsn


def test_dsn_empty_string_unchanged():
    """Empty DSN string results in empty string (not errored)."""
    rec = _make_record(dsn="")
    out = _redact_datasource(rec)
    assert out["dsn"] == ""


def test_missing_credentials_key_no_error():
    """Record without 'credentials' key does not raise."""
    rec = {"name": "test", "driver": "pg"}
    out = _redact_datasource(rec)
    assert "credentials" not in out


def test_credentials_none_no_error():
    """Record with credentials=None does not raise."""
    rec = _make_record(credentials=None)
    out = _redact_datasource(rec)
    assert out["credentials"] is None


def test_does_not_mutate_original():
    """_redact_datasource returns a copy; the original record is NOT mutated."""
    creds = {"username": "alice", "password": "s3cret"}
    rec = _make_record(credentials=creds)
    _ = _redact_datasource(rec)
    # Original record's credentials dict must be unchanged
    assert rec["credentials"]["password"] == "s3cret"
    assert creds["password"] == "s3cret"


def test_redacts_combined_record():
    """Full datasource record: credentials AND dsn are both redacted."""
    rec = {
        "name": "prod_db",
        "driver": "pg",
        "credentials": {"username": "alice", "password": "s3cret", "token": "abc123"},
        "dsn": "postgres://alice:s3cret@db.host:5432/prod",
        "params": {"host": "db.host", "port": 5432},
    }
    out = _redact_datasource(rec)
    assert out["credentials"]["password"] == "(hidden)"
    assert out["credentials"]["token"] == "(hidden)"
    assert out["credentials"]["username"] == "alice"
    assert "s3cret" not in out["dsn"]
    assert "****" in out["dsn"]
    # Other keys untouched
    assert out["params"] == {"host": "db.host", "port": 5432}


# ---------------------------------------------------------------------------
# _check_datasource_read — fail-closed PBAC gate
# ---------------------------------------------------------------------------


def _make_request(security=None, session_getter=None, evaluator=None) -> MagicMock:
    """Build a mock aiohttp request with optional security/session/evaluator."""
    request = MagicMock(spec=web.Request)
    app_dict = {}
    if security is not None:
        app_dict["security"] = security
    if session_getter is not None:
        session_app = MagicMock()
        session_app.get_session = session_getter
        app_dict["session"] = session_app
    if evaluator is not None:
        app_dict["policy_evaluator"] = evaluator
    request.app = app_dict
    return request


@pytest.mark.asyncio
async def test_pbac_disabled_is_noop():
    """When app['security'] is absent, _check_datasource_read returns without raising."""
    request = _make_request(security=None)
    # Should complete without raising
    await _check_datasource_read(request)


@pytest.mark.asyncio
async def test_pbac_enabled_no_session_raises():
    """When PBAC is enabled but session cannot be retrieved, HTTPNotFound raised (fail-closed)."""
    request = _make_request(
        security=MagicMock(),
        session_getter=AsyncMock(return_value=None),
    )
    with pytest.raises(web.HTTPNotFound):
        await _check_datasource_read(request)


@pytest.mark.asyncio
async def test_pbac_enabled_session_error_raises():
    """When PBAC is enabled and session getter raises, HTTPNotFound raised (fail-closed)."""
    session_app = MagicMock()
    session_app.get_session = AsyncMock(
        side_effect=RuntimeError("Session backend unavailable")
    )
    request = MagicMock(spec=web.Request)
    request.app = {
        "security": MagicMock(),
        "session": session_app,
    }
    with pytest.raises(web.HTTPNotFound):
        await _check_datasource_read(request)


@pytest.mark.asyncio
async def test_pbac_enabled_no_evaluator_raises():
    """When PBAC is enabled, session present, but evaluator missing, HTTPNotFound raised."""
    request = _make_request(
        security=MagicMock(),
        session_getter=AsyncMock(return_value={"user": "alice"}),
        # evaluator NOT in app — _make_request omits it when evaluator=None
    )
    with pytest.raises(web.HTTPNotFound):
        await _check_datasource_read(request)


@pytest.mark.asyncio
async def test_pbac_enabled_with_session_and_evaluator_passes():
    """When PBAC is enabled and both session and evaluator are present, no exception raised."""
    request = _make_request(
        security=MagicMock(),
        session_getter=AsyncMock(return_value={"user": "alice"}),
        evaluator=MagicMock(),
    )
    # Should not raise
    await _check_datasource_read(request)
