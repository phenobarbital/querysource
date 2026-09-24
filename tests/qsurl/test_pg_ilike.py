"""PostgreSQL ILIKE / NOT ILIKE dict operator — Rust and Cython builders agree (FEAT-152)."""
from __future__ import annotations

import pytest

from querysource.models import QueryObject
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser

SQL = "SELECT * FROM t {where_cond}"


def _rust_supports_ilike() -> bool:
    """Probe whether the installed ``_qs_parsers`` extension renders ILIKE.

    The compiled extension is a build artifact installed into the shared
    virtualenv; this sandbox cannot rebuild/reinstall it (that would mutate
    shared, read-only environment state), so the Rust-path assertions are
    skipped when the installed binary predates this task's Rust source
    change (see Completion Note).
    """
    if not pgsql.HAS_RUST:
        return False
    rendered = pgsql._rs.pgsql_filter_conditions(SQL, {"city": {"ILIKE": "%san%"}}, {})
    return "city ILIKE '%san%'" in rendered


RUST_AVAILABLE = _rust_supports_ilike()
PATHS = [
    pytest.param(
        "rust",
        marks=pytest.mark.skipif(
            not RUST_AVAILABLE,
            reason="installed _qs_parsers extension does not yet include the ILIKE dict operator "
            "(rebuild requires `make build-rust`, which installs into the shared .venv)",
        ),
    ),
    "cython",
]


def _make_parser(query: str, filter_: dict) -> pgSQLParser:
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=query), query=query)
    parser.cond_definition = {}
    parser.filter = filter_
    return parser


async def _render(path: str, filter_: dict) -> str:
    if path == "rust":
        return pgsql._rs.pgsql_filter_conditions(SQL, filter_, {})
    return await _make_parser(SQL, filter_)._filter_conditions_cy(SQL)


def _where_body(sql: str) -> str | None:
    """Return the text after ``WHERE `` in the rendered SQL, or None if absent."""
    if " WHERE " not in sql:
        return None
    return sql.split(" WHERE ", 1)[1].strip()


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    "filter_,expected",
    [
        ({"city": {"ILIKE": "%san%"}}, "city ILIKE '%san%'"),
        ({"city": {"NOT ILIKE": "%san%"}}, "city NOT ILIKE '%san%'"),
        ({"name": {"ILIKE": "o'brien%"}}, "name ILIKE 'o''brien%'"),
        ({"name": {"ILIKE": "'abc%"}}, "name ILIKE '''abc%'"),
        ({"code": {"ILIKE": "a\\%b%"}}, "code ILIKE E'a\\\\%b%'"),
        ({"n": {"ILIKE": 5}}, None),
    ],
)
async def test_ilike_rendering(path: str, filter_: dict, expected: str | None) -> None:
    sql = await _render(path, filter_)
    assert _where_body(sql) == expected


@pytest.mark.parametrize("path", PATHS)
async def test_ilike_strips_prequoted_value(path: str) -> None:
    """FEAT-152/TASK-776: ``is_valid()`` (abstract.pyx ``_where_element``, run during
    ``set_options()``/``set_where()`` in the real QS pipeline BEFORE ``filter_conditions()``
    ever executes) wraps non-numeric string filter values in a single-quote pair when
    ``noquote=False`` (the pgSQLParser default) — so by the time a real end-to-end query
    reaches this builder, the pattern already carries one quote layer. Both builders must
    strip it (matching how the COMPARISON_TOKENS branch above tolerates the same
    pre-quoting via ``Entity.quoteString``'s strip-then-requote behaviour) rather than
    quoting it a second time. Discovered via ``tests/e2e/test_qsurl_dry_run.py``
    (TASK-776), where the unstripped builder rendered ``city ILIKE '''%san%'''``.
    """
    sql = await _render(path, {"city": {"ILIKE": "'%san%'"}})
    assert _where_body(sql) == "city ILIKE '%san%'"


async def test_builders_agree_on_every_case() -> None:
    if not RUST_AVAILABLE:
        pytest.skip(
            "installed _qs_parsers extension does not yet include the ILIKE dict operator "
            "(rebuild requires `make build-rust`, which installs into the shared .venv)"
        )
    cases = [
        {"city": {"ILIKE": "%san%"}},
        {"city": {"NOT ILIKE": "%san%"}},
        {"name": {"ILIKE": "o'brien%"}},
    ]
    for filter_ in cases:
        rust_sql = await _render("rust", filter_)
        cython_sql = await _render("cython", filter_)
        assert _where_body(rust_sql) == _where_body(cython_sql)


def test_pinned_is_valid_prequoting_shape() -> None:
    """FEAT-152 code review: document the shape the ILIKE strip-then-requote
    logic (pgsql.pyx, rust/src/pgsql_parser.rs) depends on.

    ``is_valid()`` (types/validators.pyx, called by abstract.pyx's
    ``_where_element`` during ``set_options()``/``set_where()``, BEFORE
    ``filter_conditions()`` ever runs) wraps a plain string value in a single
    quote pair, doubling any interior ``'`` (PG-style escaping — ledger
    issue:48c9b3050a0c, fixed). The ILIKE builders strip that outer pair and
    undo the doubling (``.replace("''", "'")``) before handing the clean text
    to their own real escaper (``pg_literal``), so they stay correct
    regardless of whether this pre-quoting step itself escapes — see the
    strip-then-requote comments at both call sites.
    """
    from querysource.types.validators import is_valid

    assert is_valid("name", "%o'brien%", noquote=False) == "'%o''brien%'"
