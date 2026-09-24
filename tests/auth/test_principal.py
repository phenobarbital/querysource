"""Unit tests for QSPrincipal and QueryAccessDenied (FEAT-150, TASK-748)."""
import pytest

from querysource.auth.principal import QSPrincipal
from querysource.exceptions import QueryAccessDenied, QueryException


class TestQSPrincipal:
    @pytest.mark.parametrize("uid", ["", "   ", None])
    def test_requires_user_id(self, uid):
        with pytest.raises(ValueError):
            QSPrincipal(user_id=uid)

    def test_to_userinfo_shape(self):
        principal = QSPrincipal(user_id="35", groups=["a"])
        userinfo = principal.to_userinfo()
        assert set(userinfo.keys()) == {
            "username", "user_id", "groups", "roles", "programs", "superuser",
        }
        assert userinfo["username"] == "35"
        assert userinfo["user_id"] == "35"
        assert userinfo["groups"] == ["a"]
        assert "tenant_id" not in userinfo
        assert "channel" not in userinfo

    def test_normalizes_sequences(self):
        principal = QSPrincipal(
            user_id="35", groups=["a", "b"], roles="admin", programs=("p1",)
        )
        assert principal.groups == ("a", "b")
        assert principal.roles == ("admin",)
        assert principal.programs == ("p1",)

    def test_for_authz_shape(self):
        principal = QSPrincipal.for_authz("authz_useragent")
        assert principal.is_authz is True
        assert principal.to_userinfo() == {
            "username": "authz:authz_useragent",
            "groups": ["authorized", "authz_useragent"],
            "roles": [],
        }
        user_principal = QSPrincipal(user_id="35")
        assert user_principal.is_authz is False

    def test_authz_rejects_extra_claims(self):
        with pytest.raises(ValueError):
            QSPrincipal(
                user_id="authz:ip",
                username="authz:ip",
                groups=("authorized", "ip", "extra"),
                authz_backend="ip",
            )
        with pytest.raises(ValueError):
            QSPrincipal(
                user_id="authz:ip",
                username="authz:ip",
                groups=("authorized", "ip"),
                superuser=True,
                authz_backend="ip",
            )
        with pytest.raises(ValueError):
            QSPrincipal.for_authz("")

    def test_log_fields(self):
        principal = QSPrincipal(user_id="35", tenant_id="client_b", channel="ui")
        assert principal.log_fields() == {
            "principal": "35",
            "principal_tenant": "client_b",
            "channel": "ui",
        }


def test_query_access_denied_is_query_exception():
    exc = QueryAccessDenied()
    assert isinstance(exc, QueryException)
    assert exc.code == 404
    assert exc.message == "Query not available."
