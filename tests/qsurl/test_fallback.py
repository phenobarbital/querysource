"""Lark fallback parser: one test per lowering rule (spec §3 M3 rules 1-10, TASK-766)."""
from __future__ import annotations

import pytest

from querysource.qsurl import QSUrlError
from querysource.qsurl._fallback import parse


def test_example_query():
    ir = parse(
        "/queries/hisense_stores{store_id,name,city}?state_code='CA'&opened>=2024-01-01:sort(name):top(50)"
    )
    assert ir["requires"] == ["select", "filter", "sort", "limit"]
    assert ir["filter"]["and"][1] == {
        "column": "opened",
        "expression": ">=",
        "value": "2024-01-01",
        "dtype": "date",
    }


def test_precedence_and_flattening():
    assert parse("s?a=1|b=2&!c=3")["filter"] == {
        "or": [
            {"column": "a", "expression": "==", "value": 1},
            {
                "and": [
                    {"column": "b", "expression": "==", "value": 2},
                    {"not": {"column": "c", "expression": "==", "value": 3}},
                ]
            },
        ]
    }


def test_parens_group_correctly():
    ir = parse("s?(a=1|b=2)&c=3")
    assert len(ir["filter"]["and"][0]["or"]) == 2


def test_unknown_pipe():
    with pytest.raises(QSUrlError) as exc:
        parse("stores?state='CA':order(name)")
    assert (exc.value.kind, exc.value.offset) == ("parse", 18)
    assert exc.value.message.startswith("unknown pipeline operator `:order`")
    assert ":sort" in exc.value.message


def test_duplicate_top():
    with pytest.raises(QSUrlError) as exc:
        parse("s:top(1):top(2)")
    assert (exc.value.kind, exc.value.offset) == ("parse", 0)
    assert exc.value.message == ":top/:limit given more than once"


def test_duplicate_skip():
    with pytest.raises(QSUrlError) as exc:
        parse("s:skip(1):offset(2)")
    assert (exc.value.kind, exc.value.offset) == ("parse", 0)
    assert exc.value.message == ":skip/:offset given more than once"


def test_lowering_errors():
    # R1: text op with the column on the right.
    with pytest.raises(QSUrlError) as exc:
        parse("s?'x'~name")
    assert exc.value.kind == "lower"
    assert "left-hand side" in exc.value.message

    # R3: list with an operator other than ==/!=.
    with pytest.raises(QSUrlError) as exc:
        parse("s?name~('a','b')")
    assert exc.value.kind == "lower"
    assert "does not accept a list" in exc.value.message

    # R4: no column on either side.
    with pytest.raises(QSUrlError) as exc:
        parse("s?1=2")
    assert exc.value.kind == "lower"
    assert "needs a column" in exc.value.message

    # R4: bare literal in truthy position.
    with pytest.raises(QSUrlError) as exc:
        parse("s?1")
    assert exc.value.kind == "lower"
    assert "bare literal" in exc.value.message


def test_null_sugar():
    ir = parse("s?email!=null&phone=null&!fax&active")
    leaves = ir["filter"]["and"]
    assert leaves[0] == {"column": "email", "expression": "not_null"}
    assert leaves[1] == {"column": "phone", "expression": "is_null"}
    # R2: `!fax` is sugar for is_null — no "not" node, no "not" capability.
    assert leaves[2] == {"column": "fax", "expression": "is_null"}
    assert leaves[3] == {"column": "active", "expression": "not_null"}
    assert "not" not in ir["requires"]
    assert ir["requires"] == ["filter", "null_check"]


def test_literals_typed():
    ir = parse(
        "s?a=1&b=-2.5&c=true&d='it''s'&e=\"q\\\"q\"&f=2024-01-01T10:30:00Z&g='2024-01-01'"
    )
    leaves = ir["filter"]["and"]
    assert leaves[0]["value"] == 1 and isinstance(leaves[0]["value"], int)
    assert leaves[1]["value"] == -2.5
    assert leaves[2]["value"] is True
    assert leaves[3]["value"] == "it's"
    assert leaves[4]["value"] == 'q"q'
    assert leaves[5]["dtype"] == "datetime"
    assert leaves[6]["value"] == "2024-01-01"
    assert "dtype" not in leaves[6], "quoted date stays a plain string"


def test_keywords_do_not_steal_identifiers():
    for src, column in (("s?nullable=1", "nullable"), ("s?topic=1", "topic"), ("s?distinctive=1", "distinctive")):
        ir = parse(src)
        assert ir["filter"]["and"][0]["column"] == column


def test_whitespace_tolerated():
    a = parse("stores ? state = 'CA' & city ~ 'san' : sort( -opened , name ) : top( 5 )")
    b = parse("stores?state='CA'&city~'san':sort(-opened,name):top(5)")
    assert a == b
    assert a["sort"] == [
        {"column": "opened", "order": "desc"},
        {"column": "name", "order": "asc"},
    ]


@pytest.mark.parametrize(
    "src,offset",
    [
        ("stores?state=", 13),
        ("s{a b}", 4),
        ("s?(a=1", 6),
    ],
)
def test_generic_syntax_error_offsets(src, offset):
    with pytest.raises(QSUrlError) as exc:
        parse(src)
    assert exc.value.kind == "parse"
    assert exc.value.offset == offset


def test_functions_navigation_alias_and_flipped_comparison():
    ir = parse("s{id,store.region.name:as(region)}?lower(name)='acme'&100<price&year(opened)=2024")
    assert ir["fields"][1] == {"column": "store.region.name", "alias": "region"}
    leaves = ir["filter"]["and"]
    assert leaves[0] == {
        "column": {"fn": "lower", "args": ["name"]},
        "expression": "==",
        "value": "acme",
    }
    # `100<price` flips to `price>100`.
    assert leaves[1] == {"column": "price", "expression": ">", "value": 100}
    assert leaves[2]["column"]["fn"] == "year"
    assert ir["requires"] == ["select", "alias", "filter", "functions", "navigation"]


def test_in_list_and_text_operators():
    ir = parse(
        "s?state=('CA','NV')&name^='San'&city$='go'&zip=~'^9'&notes!~'closed'"
    )
    leaves = ir["filter"]["and"]
    assert leaves[0] == {"column": "state", "expression": "==", "value": ["CA", "NV"]}
    assert leaves[1]["expression"] == "startswith"
    assert leaves[2]["expression"] == "endswith"
    assert leaves[3]["expression"] == "regex"
    assert leaves[4]["expression"] == "not_contains"
    assert ir["requires"] == ["filter", "in_list", "text_match", "regex"]


def test_requires_declaration_ordered():
    ir = parse("s{a,b:as(c)}?x=1&!y:sort(a):top(1)")
    # Bounded by capabilities.ALL declaration order, NOT alphabetical.
    assert ir["requires"] == ["select", "alias", "filter", "null_check", "sort", "limit"]
