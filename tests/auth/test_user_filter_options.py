"""FEAT-184 TASK-950 — UserFilterDenied and user_filter_options validation."""
from __future__ import annotations

import pytest

from querysource.auth.user_filter import UserFilterOptions, parse_user_filter_options
from querysource.exceptions import ConfigError, UserFilterDenied


def test_user_filter_denied_is_403_and_generic() -> None:
    err = UserFilterDenied(reason_code="missing_attribute", slug="s")
    assert err.code == 403
    assert str(err) == "Access denied."
    assert err.reason_code == "missing_attribute" and err.slug == "s"


def test_options_defaults() -> None:
    opts = parse_user_filter_options({"rules": [{"column": "user_id", "attribute": "user_id"}]})
    assert opts.bypass == ("superuser",)
    assert opts.rules[0].operator == "eq" and opts.rules[0].required is True
    assert opts.program.restricted is False and opts.program.allow_default is True


@pytest.mark.parametrize(
    "raw",
    [
        {"rules": [{"column": "user_id", "attribute": "user_id"}], "unknown": True},
        {"rules": [{"column": "user_id", "attribute": "user_id", "unknown": True}]},
    ],
)
def test_options_rejects_unknown_keys(raw: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        parse_user_filter_options(raw)


@pytest.mark.parametrize("raw", [None, {}, {"rules": []}])
def test_options_rejects_empty_document(raw: dict[str, object] | None) -> None:
    with pytest.raises(ConfigError):
        parse_user_filter_options(raw)


def test_options_program_only_is_valid() -> None:
    opts = parse_user_filter_options({"program": {"restricted": True}})
    assert opts.rules == ()
    assert opts.program.restricted is True


@pytest.mark.parametrize("column", ["user_id; DROP", "a.b.c", ""])
def test_options_rejects_bad_column_grammar(column: str) -> None:
    with pytest.raises(ConfigError):
        parse_user_filter_options({"rules": [{"column": column, "attribute": "user_id"}]})

    opts = parse_user_filter_options({"rules": [{"column": "u.user_id", "attribute": "user_id"}]})
    assert opts.rules[0].column == "u.user_id"


def test_options_passthrough_instance() -> None:
    opts = UserFilterOptions(rules=({"column": "user_id", "attribute": "user_id"},))
    assert parse_user_filter_options(opts) is opts
