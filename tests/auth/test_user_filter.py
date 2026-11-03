"""FEAT-184 TASK-951 — compile_user_filter and helpers."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from querysource.auth.principal import QSPrincipal
from querysource.auth.user_filter import compile_user_filter, resolve_attribute, session_userinfo
from querysource.exceptions import UserFilterDenied

OPTIONS = {
    "rules": [
        {"column": "user_id", "attribute": "user_id"},
        {"column": "store_id", "attribute": "attributes.store_id", "operator": "in"},
    ],
    "program": {"restricted": True, "allow_default": True},
    "bypass": ["superuser", "admins"],
}


@pytest.fixture
def principal() -> QSPrincipal:
    """Return a regular HTTP principal."""
    return QSPrincipal(user_id="7", username="ana", groups=("sales",), programs=("epson",), channel="http")


@pytest.fixture
def userinfo() -> dict:
    """Return raw session attributes that supplement the principal."""
    return {"user_id": "999", "programs": ["epson"], "attributes": {"store_id": [11, 12]}}


def test_compile_scalar_and_list_predicates(principal: QSPrincipal, userinfo: dict) -> None:
    result = compile_user_filter(OPTIONS, program_slug="epson", principal=principal, userinfo=userinfo)
    assert result.predicates == {"user_id": "7", "store_id": [11, 12]}
    assert result.applied == ("user_id", "store_id")


def test_compile_no_principal_denies() -> None:
    with pytest.raises(UserFilterDenied) as exc:
        compile_user_filter(OPTIONS, program_slug="epson", principal=None, userinfo={})
    assert exc.value.reason_code == "no_principal" and exc.value.code == 403


def test_resolve_attribute_principal_first_then_userinfo(principal: QSPrincipal, userinfo: dict) -> None:
    assert resolve_attribute("user_id", principal, userinfo) == "7"
    assert resolve_attribute("attributes.store_id", principal, userinfo) == [11, 12]
    assert resolve_attribute("attributes.missing", principal, userinfo) is None


def test_resolve_attribute_authz_principal_claims_absent() -> None:
    principal = QSPrincipal.for_authz("pbac")
    assert resolve_attribute("user_id", principal, {}) is None
    assert resolve_attribute("groups", principal, {}) == ["authorized", "pbac"]


def test_compile_authz_principal_missing_claim_denies_with_authz_reason() -> None:
    options = {"rules": [{"column": "user_id", "attribute": "user_id"}]}
    with pytest.raises(UserFilterDenied) as exc:
        compile_user_filter(options, program_slug=None, principal=QSPrincipal.for_authz("pbac"), userinfo={})
    assert exc.value.reason_code == "authz_principal"


@pytest.mark.parametrize("missing", [None, [], ()])
def test_compile_missing_required_attribute_denies(missing: object) -> None:
    options = {"rules": [{"column": "store_id", "attribute": "store_ids"}]}
    with pytest.raises(UserFilterDenied) as exc:
        compile_user_filter(options, program_slug=None, principal=None, userinfo={"store_ids": missing})
    assert exc.value.reason_code == "missing_attribute"


def test_compile_missing_optional_attribute_skips() -> None:
    options = {"rules": [{"column": "store_id", "attribute": "store_ids", "required": False}]}
    result = compile_user_filter(options, program_slug=None, principal=None, userinfo={"username": "ana"})
    assert result.predicates == {}
    assert result.applied == ()


def test_compile_superuser_bypass() -> None:
    principal = QSPrincipal(user_id="1", superuser=True)
    result = compile_user_filter(OPTIONS, program_slug="forbidden", principal=principal, userinfo={})
    assert result.bypassed is True
    assert result.bypass_reason == "superuser"
    assert result.predicates == {}


@pytest.mark.parametrize(
    ("principal", "expected"),
    [
        (QSPrincipal(user_id="1", groups=("admins",)), "group:admins"),
        (QSPrincipal(user_id="1", roles=("Operators",)), "role:Operators"),
    ],
)
def test_compile_group_role_bypass_case_insensitive(principal: QSPrincipal, expected: str) -> None:
    options = {**OPTIONS, "bypass": ["Admins", "operators"]}
    result = compile_user_filter(options, program_slug="forbidden", principal=principal, userinfo={})
    assert result.bypass_reason == expected


def test_check_program_default_allowed() -> None:
    options = {"program": {"restricted": True, "allow_default": True}}
    result = compile_user_filter(options, program_slug="DEFAULT", principal=None, userinfo={"username": "ana"})
    assert result.predicates == {}


def test_check_program_default_denied_when_not_allowed() -> None:
    options = {"program": {"restricted": True, "allow_default": False}}
    with pytest.raises(UserFilterDenied) as exc:
        compile_user_filter(options, program_slug="default", principal=None, userinfo={"programs": ["epson"]})
    assert exc.value.reason_code == "program_restricted"


@dataclass
class Program:
    """Program object accepted by the normalizer."""

    slug: str


@pytest.mark.parametrize(
    "programs",
    ["EPSON", {"slug": "Epson"}, {"name": "epson"}, Program("EpSoN")],
)
def test_check_program_normalizes_supported_values(programs: object) -> None:
    options = {"program": {"restricted": True}}
    result = compile_user_filter(options, program_slug="EPSON", principal=None, userinfo={"programs": programs})
    assert result.predicates == {}


def test_check_program_restricted_denies() -> None:
    options = {"program": {"restricted": True}}
    with pytest.raises(UserFilterDenied) as exc:
        compile_user_filter(options, program_slug="canon", principal=None, userinfo={"programs": ["epson"]})
    assert exc.value.reason_code == "program_restricted"


def test_session_userinfo_fallback_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("querysource.auth.user_filter.AUTH_SESSION_OBJECT", "navigator_user")
    assert session_userinfo({"user": {"user_id": "7"}}) == {"user_id": "7"}
    assert session_userinfo({"navigator_user": "invalid", "user": {"user_id": "7"}}) == {}
    assert session_userinfo(None) == {}


def test_compile_does_not_mutate_inputs(principal: QSPrincipal, userinfo: dict) -> None:
    options = {
        "rules": [
            {"column": "user_id", "attribute": "user_id"},
            {"column": "store_id", "attribute": "attributes.store_id", "operator": "in"},
        ],
        "program": {"restricted": True},
    }
    original_options = {
        "rules": [dict(rule) for rule in options["rules"]],
        "program": dict(options["program"]),
    }
    original_userinfo = {
        "user_id": userinfo["user_id"],
        "programs": list(userinfo["programs"]),
        "attributes": {"store_id": list(userinfo["attributes"]["store_id"])},
    }
    compile_user_filter(options, program_slug="epson", principal=principal, userinfo=userinfo)
    assert options == original_options
    assert userinfo == original_userinfo
