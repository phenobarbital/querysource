"""Pure-Python qsurl parser (Lark) — used when the Rust extension is unavailable.

Produces the same IR dict and the same ``QSUrlError`` as ``querysource.qsurl._qsurl``.
Mirrors ``rust/qsurl/src/parser.rs`` (grammar) and ``rust/qsurl/src/ir.rs`` (lowering)
rule for rule; see ``grammar.lark``'s header for the LALR/contextual-lexer notes.
"""
from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

from lark import Lark, Token, Transformer
from lark.exceptions import UnexpectedCharacters, UnexpectedInput, UnexpectedToken, VisitError

from . import capabilities
from .errors import QSUrlError

GRAMMAR_PATH = files("querysource.qsurl") / "grammar.lark"
PIPE_OPERATORS: tuple[str, ...] = ("sort", "top", "limit", "skip", "offset", "distinct")

# CmpOp token text -> canonical `expression` string (mirrors ast.rs CmpOp::as_str / cmp_op()).
_CMP_MAP: dict[str, str] = {
    "==": "==",
    "=": "==",
    "!=": "!=",
    "<": "<",
    "<=": "<=",
    ">": ">",
    ">=": ">=",
    "~": "contains",
    "!~": "not_contains",
    "^=": "startswith",
    "$=": "endswith",
    "=~": "regex",
}
# Flippable (symmetric) comparison ops only; text ops have a fixed side (ir.rs CmpOp::flipped).
_FLIPPED: dict[str, str] = {"==": "==", "!=": "!=", "<": ">", "<=": ">=", ">": "<", ">=": "<="}


class _ParseIssue(Exception):
    """Internal signal for a semantic "parse"-kind error found while walking the tree.

    Used for the R8 pipeline-operator checks (unknown operator, duplicate :top/:limit
    or :skip/:offset), which the grammar accepts syntactically but which the reference
    crate reports as `kind: "parse"`, not `kind: "lower"`.
    """

    def __init__(self, offset: int, message: str) -> None:
        super().__init__(message)
        self.offset = offset
        self.message = message


@lru_cache(maxsize=1)
def _parser() -> Lark:
    """Compile the grammar once per process."""
    return Lark(GRAMMAR_PATH.read_text(encoding="utf-8"), parser="lalr", propagate_positions=True)


def _pointer(src: str, offset: int) -> str:
    """Return ``src`` plus a caret line under character ``offset`` (Rust ``pointer``)."""
    return f"{src}\n{' ' * len(src[:offset])}^"


def _describe_error(err: UnexpectedInput, src: str) -> tuple[int, str | None, list[str]]:
    """Return ``(offset, found, expected)`` for a Lark grammar-level error (R10)."""
    if isinstance(err, UnexpectedToken):
        tok = err.token
        expected = sorted(str(e) for e in err.expected) if err.expected else []
        if tok.type == "$END":
            return len(src), None, expected
        offset = tok.start_pos if tok.start_pos is not None else len(src)
        return offset, str(tok), expected
    if isinstance(err, UnexpectedCharacters):
        offset = err.pos_in_stream if err.pos_in_stream is not None else len(src)
        found = err.char if getattr(err, "char", None) else None
        allowed = sorted(str(a) for a in err.allowed) if getattr(err, "allowed", None) else []
        return offset, found, allowed
    return len(src), None, []


class _ToQuery(Transformer):
    """Parse tree -> intermediate query dict (no lowering, no validation).

    Operands are tagged tuples: ``("path", [str, ...])``, ``("literal", value, dtype)``,
    ``("list", [value, ...])``, ``("call", name, [operand, ...])``. Filter expressions are
    tagged tuples too: ``("compare", lhs, op, rhs)``, ``("truthy", operand)``,
    ``("not", expr)``, ``("and", [expr, ...])``, ``("or", [expr, ...])``.
    """

    def prefix(self, _children):
        return None

    def path(self, children):
        return ("path", [str(c) for c in children])

    def field(self, children):
        path = children[0][1]
        alias = str(children[1]) if len(children) > 1 else None
        return {"path": path, "alias": alias}

    def selection(self, children):
        return ("selection", list(children))

    def filter(self, children):
        return ("filter", children[0])

    def literal(self, children):
        tok: Token = children[0]
        t = tok.type
        if t == "NULL":
            return ("literal", None, None)
        if t == "TRUE":
            return ("literal", True, None)
        if t == "FALSE":
            return ("literal", False, None)
        if t == "DATETIME":
            return ("literal", str(tok), "datetime")
        if t == "DATE":
            return ("literal", str(tok), "date")
        if t == "NUMBER":
            s = str(tok)
            value = float(s) if "." in s else int(s)
            return ("literal", value, None)
        if t == "STRING":
            s = str(tok)
            if s[0] == "'":
                unescaped = s[1:-1].replace("''", "'")
            else:
                unescaped = s[1:-1].replace('\\"', '"')
            return ("literal", unescaped, None)
        raise AssertionError(f"unexpected literal token type {t}")  # pragma: no cover

    def list(self, children):
        return ("list", [item[1] for item in children])

    def call(self, children):
        name = str(children[0])
        return ("call", name, list(children[1:]))

    def comparison(self, children):
        if len(children) == 1:
            return ("truthy", children[0])
        lhs, cmp_tok, rhs = children
        return ("compare", lhs, _CMP_MAP[str(cmp_tok)], rhs)

    def not_expr(self, children):
        return ("not", children[0])

    def and_expr(self, children):
        return ("and", list(children))

    def or_expr(self, children):
        return ("or", list(children))

    def sort_key(self, children):
        descending = False
        idx = 0
        if isinstance(children[0], Token) and children[0].type == "SORT_DIR":
            descending = str(children[0]) == "-"
            idx = 1
        return (children[idx][1], descending)

    def sort_pipe(self, children):
        return ("sort", list(children[1:]))  # children[0] is the SORT_KW token

    def top_pipe(self, children):
        return ("top", int(children[-1]))

    def skip_pipe(self, children):
        return ("skip", int(children[-1]))

    def distinct_pipe(self, _children):
        return ("distinct", None)

    def unknown_pipe(self, children):
        ident: Token = children[0]
        offset = ident.start_pos if ident.start_pos is not None else 0
        known = ", ".join(f":{k}" for k in PIPE_OPERATORS)
        raise _ParseIssue(offset, f"unknown pipeline operator `:{ident}`; expected one of: {known}")

    def pipe(self, children):
        return ("pipe", children[0])

    def start(self, children):
        slug: str | None = None
        fields: list[dict] = []
        filter_expr = None
        sort: list[tuple[list[str], bool]] = []
        limit: int | None = None
        offset: int | None = None
        distinct = False
        for child in children:
            if child is None:
                continue
            if isinstance(child, Token):
                slug = str(child)
                continue
            tag, payload = child
            if tag == "selection":
                fields = payload
            elif tag == "filter":
                filter_expr = payload
            elif tag == "pipe":
                kind, value = payload
                if kind == "sort":
                    sort.extend(value)
                elif kind == "top":
                    if limit is not None:
                        raise _ParseIssue(0, ":top/:limit given more than once")
                    limit = value
                elif kind == "skip":
                    if offset is not None:
                        raise _ParseIssue(0, ":skip/:offset given more than once")
                    offset = value
                elif kind == "distinct":
                    distinct = True
        return {
            "slug": slug,
            "fields": fields,
            "filter": filter_expr,
            "sort": sort,
            "limit": limit,
            "offset": offset,
            "distinct": distinct,
        }


def _is_column(operand: tuple) -> bool:
    return operand[0] in ("path", "call")


def _lower_operand(operand: tuple, requires: set[str]):
    tag = operand[0]
    if tag == "path":
        segments = operand[1]
        if len(segments) > 1:
            requires.add(capabilities.NAVIGATION)
        return ".".join(segments)
    if tag == "literal":
        return operand[1]
    if tag == "list":
        requires.add(capabilities.IN_LIST)
        return list(operand[1])
    if tag == "call":
        requires.add(capabilities.FUNCTIONS)
        return {"fn": operand[1], "args": [_lower_operand(a, requires) for a in operand[2]]}
    raise AssertionError(f"unknown operand tag {tag}")  # pragma: no cover


def _leaf(column: tuple, op: str, value: tuple, requires: set[str]) -> dict:
    """Leaf ``{column, expression, value}`` with the same normalisations as ir.rs::leaf."""
    m: dict = {"column": _lower_operand(column, requires)}

    if op == "==" and value == ("literal", None, None):
        requires.add(capabilities.NULL_CHECK)
        m["expression"] = "is_null"
    elif op == "!=" and value == ("literal", None, None):
        requires.add(capabilities.NULL_CHECK)
        m["expression"] = "not_null"
    elif op in ("==", "!=") and value[0] == "list":
        m["expression"] = op
        m["value"] = _lower_operand(value, requires)
    elif value[0] == "list":
        raise QSUrlError(
            "lower", f"operator `{op}` does not accept a list; use `=` or `!=` for membership"
        )
    elif op == "regex":
        requires.add(capabilities.REGEX)
        m["expression"] = op
        m["value"] = _lower_operand(value, requires)
    elif op in ("contains", "not_contains", "startswith", "endswith"):
        requires.add(capabilities.TEXT_MATCH)
        m["expression"] = op
        m["value"] = _lower_operand(value, requires)
    else:
        m["expression"] = op
        m["value"] = _lower_operand(value, requires)

    if value[0] == "literal" and value[2] in ("date", "datetime"):
        m["dtype"] = value[2]
    return m


def _lower_expr(expr: tuple, requires: set[str]) -> dict:
    tag = expr[0]
    if tag == "compare":
        _, lhs, op, rhs = expr
        if _is_column(lhs):
            return _leaf(lhs, op, rhs, requires)
        if _is_column(rhs):
            flipped = _FLIPPED.get(op)
            if flipped is None:
                raise QSUrlError("lower", f"operator `{op}` requires the column on the left-hand side")
            return _leaf(rhs, flipped, lhs, requires)
        raise QSUrlError("lower", "a comparison needs a column or function on at least one side")
    if tag == "truthy":
        operand = expr[1]
        if not _is_column(operand):
            raise QSUrlError("lower", "a bare literal is not a condition; compare it against a column")
        requires.add(capabilities.NULL_CHECK)
        return {"column": _lower_operand(operand, requires), "expression": "not_null"}
    if tag == "not":
        inner = expr[1]
        if inner[0] == "truthy" and _is_column(inner[1]):
            requires.add(capabilities.NULL_CHECK)
            return {"column": _lower_operand(inner[1], requires), "expression": "is_null"}
        requires.add(capabilities.NOT)
        return {"not": _lower_expr(inner, requires)}
    if tag == "and":
        return {"and": [_lower_expr(i, requires) for i in expr[1]]}
    if tag == "or":
        requires.add(capabilities.OR)
        return {"or": [_lower_expr(i, requires) for i in expr[1]]}
    raise AssertionError(f"unknown expr tag {tag}")  # pragma: no cover


def _lower(query: dict) -> dict:
    """Apply rules R1-R7 and return the IR dict (keys as in the Rust output)."""
    needed: set[str] = set()

    fields = []
    for f in query["fields"]:
        path = f["path"]
        if len(path) > 1:
            needed.add(capabilities.NAVIGATION)
        if f["alias"] is None:
            fields.append(".".join(path))
        else:
            needed.add(capabilities.ALIAS)
            fields.append({"column": ".".join(path), "alias": f["alias"]})
    if fields:
        needed.add(capabilities.SELECT)

    filter_expr = query["filter"]
    if filter_expr is not None:
        needed.add(capabilities.FILTER)
        v = _lower_expr(filter_expr, needed)
        filter_json = v if ("and" in v or "or" in v) else {"and": [v]}
    else:
        filter_json = None

    sort = []
    for path, descending in query["sort"]:
        if len(path) > 1:
            needed.add(capabilities.NAVIGATION)
        sort.append({"column": ".".join(path), "order": "desc" if descending else "asc"})
    if sort:
        needed.add(capabilities.SORT)

    if query["limit"] is not None:
        needed.add(capabilities.LIMIT)
    if query["offset"] is not None:
        needed.add(capabilities.OFFSET)
    if query["distinct"]:
        needed.add(capabilities.DISTINCT)

    requires = [cap for cap in capabilities.ALL if cap in needed]

    return {
        "slug": query["slug"],
        "fields": fields,
        "filter": filter_json,
        "sort": sort,
        "limit": query["limit"],
        "offset": query["offset"],
        "distinct": query["distinct"],
        "requires": requires,
    }


def parse(src: str) -> dict:
    """Lark implementation of ``querysource.qsurl.parse`` (same IR, same QSUrlError).

    Raises:
        QSUrlError: kind "parse" (grammar, duplicate/unknown pipes) or "lower".
    """
    src = src.strip()
    try:
        tree = _parser().parse(src)
    except UnexpectedInput as err:
        offset, found, expected = _describe_error(err, src)
        what = f"unexpected `{found}`" if found is not None else "unexpected end of query"
        message = f"{what} at position {offset}"
        if expected:
            message = f"{message}; expected {', '.join(expected)}"
        raise QSUrlError(
            "parse", message, offset=offset, found=found, expected=expected, pointer=_pointer(src, offset)
        ) from err
    try:
        query = _ToQuery().transform(tree)
    except VisitError as visit_err:
        issue = visit_err.orig_exc
        if not isinstance(issue, _ParseIssue):
            raise
        raise QSUrlError(
            "parse", issue.message, offset=issue.offset, pointer=_pointer(src, issue.offset)
        ) from issue
    return _lower(query)


def requires(src: str) -> list[str]:
    """``parse(src)["requires"]``."""
    return list(parse(src)["requires"])
