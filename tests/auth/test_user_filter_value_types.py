"""FEAT-184 TASK-959 — identity values are type-restricted before reaching filter_conditions."""

from __future__ import annotations

import logging

import pytest

from querysource.auth.principal import QSPrincipal
from querysource.auth.user_filter import compile_user_filter
from querysource.exceptions import UserFilterDenied


@pytest.fixture
def principal() -> QSPrincipal:
    """Return a regular HTTP principal."""
    return QSPrincipal(user_id="7", username="ana", groups=("sales",), programs=("epson",), channel="http")


def _compile(value: object, *, required: bool = True, principal: QSPrincipal, operator: str = "eq"):
    options = {"rules": [{"column": "store_id", "attribute": "attributes.store_id",
                          "required": required, "operator": operator}]}
    userinfo = {"attributes": {"store_id": value}}
    return compile_user_filter(options, program_slug="epson", principal=principal, userinfo=userinfo, slug="s")


DENIED = [
    "!42",
    "null",
    "NULL",
    "!null",
    "BETWEEN 1 AND 5",
    "NOT BETWEEN 1 AND 5",
    "IS NOT",
    ">=",
    "",
    {">": 0},
    ["!=", "x"],
    ["IS NOT", "null"],
    [["a"]],
    [None],
    [1, {"a": 1}],
    True,
    object(),
]


@pytest.mark.parametrize("value", DENIED)
def test_operator_shaped_values_are_denied(value: object, principal: QSPrincipal) -> None:
    with pytest.raises(UserFilterDenied) as exc:
        _compile(value, principal=principal)
    assert exc.value.reason_code == "invalid_attribute"
    assert exc.value.code == 403


@pytest.mark.parametrize("value", DENIED)
def test_denied_even_when_rule_not_required(value: object, principal: QSPrincipal) -> None:
    with pytest.raises(UserFilterDenied) as exc:
        _compile(value, required=False, principal=principal)
    assert exc.value.reason_code == "invalid_attribute"


@pytest.mark.parametrize("value", [[], None])
def test_empty_or_none_keeps_missing_semantics(value: object, principal: QSPrincipal) -> None:
    """Empty/absent attributes stay 'missing' (required denies, optional skips), not invalid."""
    with pytest.raises(UserFilterDenied) as exc:
        _compile(value, principal=principal)
    assert exc.value.reason_code == "missing_attribute"
    assert _compile(value, required=False, principal=principal).predicates == {}


@pytest.mark.parametrize("value", [42, "42", "store-a", 3.5])
def test_plain_scalars_pass_through(value: object, principal: QSPrincipal) -> None:
    assert _compile(value, principal=principal).predicates == {"store_id": value}


@pytest.mark.parametrize("value", [[1, 2], ["a", "b"], ("a", "b")])
def test_flat_scalar_lists_pass_through(value: object, principal: QSPrincipal) -> None:
    assert _compile(value, principal=principal).predicates == {"store_id": list(value)}


def test_scalar_with_in_operator_is_wrapped(principal: QSPrincipal) -> None:
    assert _compile("a", principal=principal, operator="in").predicates == {"store_id": ["a"]}


def test_denial_log_never_contains_value(principal: QSPrincipal, caplog: pytest.LogCaptureFixture) -> None:
    secret = "!secret-identity-value"
    with caplog.at_level(logging.WARNING):
        with pytest.raises(UserFilterDenied):
            _compile(secret, principal=principal)
    assert "invalid attribute value type" in caplog.text
    assert "secret-identity-value" not in caplog.text
