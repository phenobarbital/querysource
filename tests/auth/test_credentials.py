"""Unit tests for querysource.auth.credentials — CredentialResolver."""
import logging
import pytest
from querysource.auth import CredentialResolver, ResolvedCredentials


@pytest.fixture
def resolver():
    """Fresh CredentialResolver for each test."""
    return CredentialResolver()


class TestSanitize:
    def test_email(self):
        assert CredentialResolver.sanitize("john.doe@acme.com") == "JOHN_DOE_ACME_COM"

    def test_dashes(self):
        assert CredentialResolver.sanitize("Some-User") == "SOME_USER"

    def test_already_clean(self):
        assert CredentialResolver.sanitize("ALICE") == "ALICE"

    def test_mixed(self):
        assert CredentialResolver.sanitize("user.name-123@org.com") == "USER_NAME_123_ORG_COM"


class TestResolve:
    def test_user_override_full(self, resolver, monkeypatch):
        for k, v in {
            "PG_JOHN_HOST": "h",
            "PG_JOHN_PORT": "5432",
            "PG_JOHN_USER": "u",
            "PG_JOHN_PASSWORD": "p",
            "PG_JOHN_DATABASE": "d",
        }.items():
            monkeypatch.setenv(k, v)
        result = resolver.resolve("PG", {"username": "john"})
        assert result is not None
        assert result.source == "user-override"
        assert result.host == "h"
        assert result.port == 5432
        assert result.user == "u"
        assert result.password == "p"
        assert result.database == "d"

    def test_user_override_partial_falls_through(self, resolver, monkeypatch):
        monkeypatch.setenv("PG_JANE_HOST", "h")  # only one of five
        for k, v in {
            "PG_HOST": "default-h",
            "PG_PORT": "5432",
            "PG_USER": "u",
            "PG_PASSWORD": "p",
            "PG_DATABASE": "d",
        }.items():
            monkeypatch.setenv(k, v)
        result = resolver.resolve("PG", {"username": "jane"})
        assert result is not None
        assert result.source.startswith("default:")

    def test_profile_from_policy(self, resolver, monkeypatch):
        for k, v in {
            "PG_TIER1_HOST": "h",
            "PG_TIER1_PORT": "5432",
            "PG_TIER1_USER": "u",
            "PG_TIER1_PASSWORD": "p",
            "PG_TIER1_DATABASE": "d",
        }.items():
            monkeypatch.setenv(k, v)
        result = resolver.resolve("PG", session=None, credential_profile="tier1")
        assert result is not None
        assert result.source == "profile:tier1"

    def test_default_tier(self, resolver, monkeypatch):
        for k, v in {
            "PG_HOST": "h",
            "PG_PORT": "5432",
            "PG_USER": "u",
            "PG_PASSWORD": "p",
            "PG_DATABASE": "d",
        }.items():
            monkeypatch.setenv(k, v)
        result = resolver.resolve("PG", session={})
        assert result is not None
        assert result.source == "default:PG"

    def test_no_creds_returns_none(self, resolver, monkeypatch):
        for k in ("PG_HOST", "PG_PORT", "PG_USER", "PG_PASSWORD", "PG_DATABASE"):
            monkeypatch.delenv(k, raising=False)
        assert resolver.resolve("PG", session=None) is None

    def test_no_session_returns_none_without_env(self, resolver, monkeypatch):
        for k in ("PG_HOST", "PG_PORT", "PG_USER", "PG_PASSWORD", "PG_DATABASE"):
            monkeypatch.delenv(k, raising=False)
        assert resolver.resolve("PG", session=None, credential_profile=None) is None

    def test_invalid_port_falls_through(self, resolver, monkeypatch):
        for k, v in {
            "PG_BOB_HOST": "h",
            "PG_BOB_PORT": "not-a-number",
            "PG_BOB_USER": "u",
            "PG_BOB_PASSWORD": "p",
            "PG_BOB_DATABASE": "d",
        }.items():
            monkeypatch.setenv(k, v)
        # Should fall through, not raise
        result = resolver.resolve("PG", {"username": "bob"})
        assert result is None or result.source.startswith("default:")

    def test_session_with_user_id_fallback(self, resolver, monkeypatch):
        for k, v in {
            "PG_SVC1_HOST": "h",
            "PG_SVC1_PORT": "5432",
            "PG_SVC1_USER": "u",
            "PG_SVC1_PASSWORD": "p",
            "PG_SVC1_DATABASE": "d",
        }.items():
            monkeypatch.setenv(k, v)
        # Session without 'username' but with 'user_id'
        result = resolver.resolve("PG", {"user_id": "svc1"})
        assert result is not None
        assert result.source == "user-override"


class TestResolverWarnings:
    def test_partial_set_warning_dedup(self, monkeypatch, caplog):
        """Same partial set hit twice -> warning logged at most once per resolver instance."""
        import logging
        from querysource.auth import CredentialResolver
        # Provide only one of the five required keys so partial-set is triggered
        monkeypatch.setenv("PG_BOB_HOST", "h")
        for k in ("PG_BOB_PORT", "PG_BOB_USER", "PG_BOB_PASSWORD", "PG_BOB_DATABASE"):
            monkeypatch.delenv(k, raising=False)
        # Remove default tier too so we get None (can't fall through)
        for k in ("PG_HOST", "PG_PORT", "PG_USER", "PG_PASSWORD", "PG_DATABASE"):
            monkeypatch.delenv(k, raising=False)
        resolver = CredentialResolver()
        with caplog.at_level(logging.WARNING):
            resolver.resolve("PG", {"username": "bob"})
            resolver.resolve("PG", {"username": "bob"})
        # There should be at most one warning for this (prefix, user, missing-keys) tuple.
        relevant = [r for r in caplog.records
                    if "PG_BOB" in r.message or "partial" in r.message.lower()]
        assert len(relevant) <= 1, (
            f"Expected at most 1 partial-set warning, got {len(relevant)}: {[r.message for r in relevant]}"
        )
