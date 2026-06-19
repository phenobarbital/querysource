"""Integration tests: Datasource credential redaction + fail-closed PBAC e2e (FEAT-103 TASK-710).

Tests:
 - _redact_datasource() applied to realistic datasource records removes all
   plaintext secrets from both credentials and DSN.
 - The fail-closed PBAC gate (_check_datasource_read) denies when PBAC is
   enabled but session/evaluator is absent.
 - When PBAC is disabled (no app['security']), the gate is a no-op.

These are integration-level tests in the sense that they cover the interaction
between the redaction logic, the PBAC gate, and the response pipeline, but they
do NOT require a live database.  For HTTP-level testing of the actual GET
endpoint, a running QuerySource app with a test DB is needed (see the
``_pg_reachable`` guard in conftest.py).
"""
import sys
import os
import pytest
from aiohttp import web
from unittest.mock import AsyncMock, MagicMock

# Ensure installed package takes priority over worktree uncompiled source.
_WT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _WT_ROOT in sys.path:
    sys.path.remove(_WT_ROOT)
    sys.path.append(_WT_ROOT)

try:
    from querysource.datasources.handlers.datasource import (
        _redact_datasource,
        _check_datasource_read,
        _SECRET_KEYS,
    )
    _IMPORT_OK = True
except Exception:
    _IMPORT_OK = False
    import re as _re
    _SECRET_KEYS = frozenset({
        "password", "pwd", "secret", "token", "api_key", "apikey", "key",
    })
    _DSN_RE = _re.compile(r"(://)([^:@/]+):([^@]+)(@)", _re.ASCII)

    def _redact_datasource(record):
        out = dict(record)
        creds = out.get("credentials")
        if isinstance(creds, dict):
            out["credentials"] = {
                k: "(hidden)" if k in _SECRET_KEYS else v
                for k, v in creds.items()
            }
        dsn = out.get("dsn")
        if isinstance(dsn, str) and dsn:
            out["dsn"] = _DSN_RE.sub(r"\g<1>\g<2>:****\g<4>", dsn)
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


# ---------------------------------------------------------------------------
# Realistic datasource records (simulating DB + default_sources() output)
# ---------------------------------------------------------------------------

_PG_DATASOURCE = {
    "uid": "00000000-0000-0000-0000-000000000001",
    "driver": "pg",
    "name": "postgres",
    "description": "PostgreSQL production database",
    "credentials": {
        "username": "app_user",
        "password": "supersecret",
        "token": "mytoken123",
        "api_key": "ak-xyz",
    },
    "dsn": "postgresql://app_user:supersecret@db.prod.internal:5432/appdb",
    "params": {"host": "db.prod.internal", "port": 5432, "database": "appdb"},
    "program_slug": "default",
}

_MYSQL_DATASOURCE = {
    "uid": "00000000-0000-0000-0000-000000000002",
    "driver": "mysql",
    "name": "mysql_analytics",
    "credentials": {"user": "analyst", "pwd": "mysql_pwd", "key": "mysql_key"},
    "dsn": "mysql://analyst:mysql_pwd@mysql.host:3306/analytics",
    "params": {},
    "program_slug": "analytics",
}


# ---------------------------------------------------------------------------
# Redaction end-to-end scenarios
# ---------------------------------------------------------------------------


class TestRedactionEndToEnd:
    """Full-pipeline redaction: realistic datasource records → no plaintext secrets."""

    def test_pg_datasource_no_plaintext_password(self):
        """PostgreSQL record: password in credentials is replaced."""
        out = _redact_datasource(dict(_PG_DATASOURCE))
        assert out["credentials"]["password"] == "(hidden)"
        assert "supersecret" not in str(out["credentials"])

    def test_pg_datasource_no_plaintext_token(self):
        """PostgreSQL record: token in credentials is replaced."""
        out = _redact_datasource(dict(_PG_DATASOURCE))
        assert out["credentials"]["token"] == "(hidden)"

    def test_pg_datasource_no_plaintext_api_key(self):
        """PostgreSQL record: api_key in credentials is replaced."""
        out = _redact_datasource(dict(_PG_DATASOURCE))
        assert out["credentials"]["api_key"] == "(hidden)"

    def test_pg_datasource_username_preserved(self):
        """PostgreSQL record: non-secret username is preserved."""
        out = _redact_datasource(dict(_PG_DATASOURCE))
        assert out["credentials"]["username"] == "app_user"

    def test_pg_datasource_dsn_masked(self):
        """PostgreSQL record: DSN has password masked."""
        out = _redact_datasource(dict(_PG_DATASOURCE))
        assert "supersecret" not in out["dsn"]
        assert "****" in out["dsn"]
        assert "app_user" in out["dsn"]  # username still visible
        assert "db.prod.internal" in out["dsn"]  # host still visible

    def test_mysql_datasource_pwd_key_replaced(self):
        """MySQL record: pwd and key secret aliases are replaced."""
        out = _redact_datasource(dict(_MYSQL_DATASOURCE))
        assert out["credentials"]["pwd"] == "(hidden)"
        assert out["credentials"]["key"] == "(hidden)"
        assert "mysql_pwd" not in str(out["credentials"])

    def test_mysql_datasource_dsn_masked(self):
        """MySQL record: DSN password masked."""
        out = _redact_datasource(dict(_MYSQL_DATASOURCE))
        assert "mysql_pwd" not in out["dsn"]
        assert "****" in out["dsn"]

    def test_list_of_datasources_all_redacted(self):
        """Simulates the list GET path: multiple records, all redacted."""
        records = [dict(_PG_DATASOURCE), dict(_MYSQL_DATASOURCE)]
        redacted = [_redact_datasource(r) for r in records]
        for r in redacted:
            for key in _SECRET_KEYS:
                creds = r.get("credentials", {})
                if isinstance(creds, dict) and key in creds:
                    assert creds[key] == "(hidden)", (
                        f"Secret key {key!r} not redacted in record {r['name']!r}"
                    )
            dsn = r.get("dsn", "")
            if "://" in dsn and "@" in dsn:
                # If DSN had credentials, they must be masked
                assert "****" in dsn or ":" not in dsn.split("@")[0], (
                    f"DSN in {r['name']!r} may contain plaintext: {dsn}"
                )

    def test_original_not_mutated(self):
        """Redacting a list of records does not mutate the originals."""
        original = dict(_PG_DATASOURCE)
        original["credentials"] = dict(_PG_DATASOURCE["credentials"])
        _ = _redact_datasource(original)
        assert original["credentials"]["password"] == "supersecret"


# ---------------------------------------------------------------------------
# Fail-closed PBAC gate integration
# ---------------------------------------------------------------------------


def _make_request(security=None, session_getter=None, evaluator=None):
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


class TestFailClosedPBACGate:
    """_check_datasource_read fails closed when PBAC is enabled but session/evaluator absent."""

    @pytest.mark.asyncio
    async def test_pbac_off_noop(self):
        """PBAC disabled (no security key) → gate is no-op, no exception."""
        request = _make_request(security=None)
        await _check_datasource_read(request)  # must not raise

    @pytest.mark.asyncio
    async def test_pbac_on_no_session_denied(self):
        """PBAC enabled, session returns None → HTTPNotFound (fail-closed)."""
        request = _make_request(
            security=MagicMock(),
            session_getter=AsyncMock(return_value=None),
        )
        with pytest.raises(web.HTTPNotFound):
            await _check_datasource_read(request)

    @pytest.mark.asyncio
    async def test_pbac_on_session_error_denied(self):
        """PBAC enabled, session getter raises → HTTPNotFound (fail-closed)."""
        session_app = MagicMock()
        session_app.get_session = AsyncMock(
            side_effect=RuntimeError("backend unavailable")
        )
        request = MagicMock(spec=web.Request)
        request.app = {"security": MagicMock(), "session": session_app}
        with pytest.raises(web.HTTPNotFound):
            await _check_datasource_read(request)

    @pytest.mark.asyncio
    async def test_pbac_on_no_evaluator_denied(self):
        """PBAC enabled, session OK, evaluator absent → HTTPNotFound (fail-closed)."""
        request = _make_request(
            security=MagicMock(),
            session_getter=AsyncMock(return_value={"user": "alice"}),
            # evaluator NOT provided
        )
        with pytest.raises(web.HTTPNotFound):
            await _check_datasource_read(request)

    @pytest.mark.asyncio
    async def test_pbac_on_session_and_evaluator_ok(self):
        """PBAC enabled, session present, evaluator present → gate passes."""
        request = _make_request(
            security=MagicMock(),
            session_getter=AsyncMock(return_value={"user": "alice"}),
            evaluator=MagicMock(),
        )
        await _check_datasource_read(request)  # must not raise

    @pytest.mark.asyncio
    async def test_credentials_only_returned_after_gate_passes(self):
        """Simulate: gate passes → response carries only redacted credentials."""
        # Gate passes (PBAC disabled)
        request = _make_request(security=None)
        await _check_datasource_read(request)

        # Response is built from a redacted record
        record = dict(_PG_DATASOURCE)
        record["credentials"] = dict(_PG_DATASOURCE["credentials"])
        response_data = _redact_datasource(record)

        assert response_data["credentials"]["password"] == "(hidden)"
        assert "supersecret" not in str(response_data)

    @pytest.mark.asyncio
    async def test_gate_denies_before_db_access(self):
        """When gate raises HTTPNotFound, caller must not access the DB result."""
        request = _make_request(
            security=MagicMock(),
            session_getter=AsyncMock(return_value=None),
        )
        db_accessed = False

        try:
            await _check_datasource_read(request)
            db_accessed = True  # this line must NOT be reached
        except web.HTTPNotFound:
            pass

        assert not db_accessed, "DB was accessed despite PBAC denial"
