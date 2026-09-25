"""Render the qsurl Lark grammar as GBNF for constrained decoding."""
from __future__ import annotations

import logging
import re
from collections import OrderedDict
from pathlib import Path

from lark import Lark
from lark.grammar import NonTerminal, Terminal
from lark.lexer import PatternStr

from ._fallback import GRAMMAR_PATH

_logger = logging.getLogger(__name__)


def _rule_name(name: str) -> str:
    """Return a GBNF-legal rule name (lowercase, '-' instead of '_')."""
    slug = re.sub(r"-+", "-", name.lower().replace("_", "-")).strip("-")
    return slug or "r"


def _quote_char(ch: str) -> str:
    """Return ``ch`` as a one-character GBNF string literal."""
    escaped = ch.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


_LITERAL_RE = re.compile(r'^"(.*)"$')


def _merge_literals(parts: list[str]) -> list[str]:
    """Merge adjacent plain quoted-literal atoms into one string (cosmetic only).

    A quantified atom (e.g. ``"a"?``) never matches ``_LITERAL_RE`` (it does not end
    in a bare closing quote), so it is never merged into a neighbour — merging must
    never change which atom a following quantifier binds to.
    """
    merged: list[str] = []
    for part in parts:
        match = _LITERAL_RE.match(part)
        if match and merged:
            prev_match = _LITERAL_RE.match(merged[-1])
            if prev_match:
                merged[-1] = f'"{prev_match.group(1)}{match.group(1)}"'
                continue
        merged.append(part)
    return merged


class _RegexToGbnf:
    """Convert the small regex subset used by ``grammar.lark``'s terminals to GBNF.

    Handles: literal characters (with escaping), ``\\d``, ``[...]`` character classes,
    ``(...)`` / ``(?:...)`` groups, ``|`` alternation, and ``?``/``*``/``+`` postfix
    quantifiers. A ``(?!...)`` negative lookahead (used by the keyword terminals to
    enforce a word boundary, e.g. ``null(?![A-Za-z0-9_])``) has no GBNF equivalent and
    is dropped: the lookahead constrains generation to a narrower language than GBNF
    can express, so the emitted grammar accepts a superset of the terminal (safe for
    constrained decoding — GBNF's job here is to keep an LLM inside the *valid* shapes,
    not to reject every string a stricter parser would; the real parsers, TASK-764/766,
    remain the source of truth for that boundary).

    Any other regex construct (backreferences, negated classes, non-greedy quantifiers,
    lookbehind, ...) raises ``ValueError`` naming the offending terminal and pattern.
    """

    def __init__(self, pattern: str, terminal_name: str) -> None:
        self._p = pattern
        self._i = 0
        self._n = len(pattern)
        self._name = terminal_name

    def convert(self) -> str:
        result = self._alt()
        if self._i != self._n:
            raise ValueError(
                f"terminal {self._name}: unsupported regex {self._p!r} (stopped at index {self._i})"
            )
        return result

    def _alt(self) -> str:
        branches = [self._seq()]
        while self._i < self._n and self._p[self._i] == "|":
            self._i += 1
            branches.append(self._seq())
        if len(branches) == 1:
            return branches[0]
        return "(" + " | ".join(branches) + ")"

    def _seq(self) -> str:
        parts: list[str] = []
        while self._i < self._n and self._p[self._i] not in "|)":
            part = self._quantified()
            if part is not None:
                parts.append(part)
        parts = _merge_literals(parts)
        return " ".join(parts) if parts else '""'

    def _quantified(self) -> str | None:
        atom = self._atom()
        if atom is None:
            return None
        if self._i < self._n and self._p[self._i] in "?*+":
            quant = self._p[self._i]
            self._i += 1
            return f"{atom}{quant}"
        return atom

    def _atom(self) -> str | None:
        c = self._p[self._i]
        if c == "(":
            self._i += 1
            if self._p[self._i : self._i + 2] == "?:":
                self._i += 2
            elif self._p[self._i : self._i + 2] == "?!":
                self._i += 2
                depth = 1
                while self._i < self._n and depth:
                    if self._p[self._i] == "(":
                        depth += 1
                    elif self._p[self._i] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    self._i += 1
                if self._i >= self._n or self._p[self._i] != ")":
                    raise ValueError(f"terminal {self._name}: unterminated lookahead in {self._p!r}")
                self._i += 1
                return None  # dropped — see class docstring
            inner = self._alt()
            if self._i >= self._n or self._p[self._i] != ")":
                raise ValueError(f"terminal {self._name}: unbalanced parens in {self._p!r}")
            self._i += 1
            return f"({inner})"
        if c == "[":
            # GBNF character classes use the same [...] / [^...] syntax as regex,
            # including negation — no conversion needed beyond copying the span.
            start = self._i
            self._i += 1
            while self._i < self._n and self._p[self._i] != "]":
                if self._p[self._i] == "\\":
                    self._i += 1
                self._i += 1
            if self._i >= self._n:
                raise ValueError(f"terminal {self._name}: unterminated class in {self._p!r}")
            self._i += 1
            return self._p[start : self._i]  # GBNF character classes use the same [...] syntax
        if c == "\\":
            self._i += 1
            esc = self._p[self._i]
            self._i += 1
            if esc == "d":
                return "[0-9]"
            return _quote_char(esc)
        self._i += 1
        return _quote_char(c)


def _terminal_to_gbnf(name: str, pattern) -> str:
    """Convert one Lark terminal pattern to a GBNF expression.

    Raises:
        ValueError: if the pattern uses a construct with no GBNF equivalent.
    """
    if isinstance(pattern, PatternStr):
        return _quote_char(pattern.value) if len(pattern.value) == 1 else (
            '"' + pattern.value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        )
    return _RegexToGbnf(pattern.value, name).convert()


def _detect_star_bodies(grouped: OrderedDict[str, list]) -> dict[str, list]:
    """Detect Lark's `(body)+` left-recursive encoding: ``rule ::= body | rule body``.

    Returns:
        A mapping of origin name to its ``body`` expansion (the symbols repeated one
        or more times), for every origin that matches this exact two-alternative shape.
    """
    star_bodies: dict[str, list] = {}
    for origin_name, rules in grouped.items():
        if len(rules) != 2:
            continue
        exp_a, exp_b = rules[0].expansion, rules[1].expansion
        for body, maybe_self_first in ((exp_a, exp_b), (exp_b, exp_a)):
            if (
                len(maybe_self_first) == len(body) + 1
                and isinstance(maybe_self_first[0], NonTerminal)
                and maybe_self_first[0].name == origin_name
                and [s.name for s in maybe_self_first[1:]] == [s.name for s in body]
            ):
                star_bodies[origin_name] = body
                break
    return star_bodies


def to_gbnf(grammar_path: Path | None = None) -> str:
    """Render the Lark grammar as GBNF with ``root ::= ws query ws``.

    Args:
        grammar_path: grammar file to convert; defaults to the packaged ``grammar.lark``.

    Returns:
        The GBNF grammar text.

    Raises:
        ValueError: if the grammar uses a Lark construct with no GBNF equivalent.
    """
    source = (
        Path(grammar_path).read_text(encoding="utf-8")
        if grammar_path
        else GRAMMAR_PATH.read_text(encoding="utf-8")
    )
    lark = Lark(source, parser="lalr")

    terminal_gbnf: dict[str, str] = {
        term.name: _terminal_to_gbnf(term.name, term.pattern) for term in lark.terminals
    }

    grouped: OrderedDict[str, list] = OrderedDict()
    for rule in lark.rules:
        grouped.setdefault(rule.origin.name, []).append(rule)

    # Lark expands every `(body)*`/`(body)+` in grammar.lark into a synthetic
    # `__foo_star_N` origin encoded as LEFT recursion (`rule ::= body | rule body`).
    # GBNF has a native `+` quantifier that says the same thing without recursion
    # (left recursion has no meaning for a GBNF/LLM-decoding consumer, and the test
    # matcher below cannot backtrack through it); detect that exact shape and inline
    # it as `(...)+ ` at every use site instead of emitting it as its own rule.
    star_bodies: dict[str, list] = _detect_star_bodies(grouped)

    def _symbol_gbnf(sym) -> str:
        if isinstance(sym, Terminal):
            return terminal_gbnf[sym.name]
        if sym.name in star_bodies:
            body_text = " ws ".join(_symbol_gbnf(s) for s in star_bodies[sym.name])
            # Lark's `%ignore WS` tolerates whitespace between *every* adjacent
            # token pair, including at a `*`/`+` repetition boundary (e.g.
            # ":sort(...) : top(...)" — a space before the second pipe's `:`) —
            # a bare GBNF `(body)+` has no separator between repetitions, so an
            # explicit `ws` is inserted between them too, not just within one.
            return f"({body_text}) (ws ({body_text}))*"
        return "query" if sym.name == "start" else _rule_name(sym.name)

    # `unknown_pipe` exists in grammar.lark purely so the Lark *parser* can capture an
    # unrecognised pipe-operator identifier for a precise R8 error message (TASK-766);
    # it is not part of the qsurl *language* — grammatically it accepts any identifier
    # at all. For GBNF (constrained decoding: the whole point is to keep an LLM inside
    # only the valid shapes), any alternative that reaches `unknown_pipe` is dropped
    # entirely, so `pipe` only ever admits `:sort`/`:top`/`:limit`/`:skip`/`:offset`/
    # `:distinct` — matching AC16's own `test_gbnf_rejects_unknown_pipe` expectation.
    excluded_origins = {"unknown_pipe"}

    def _references_excluded(rule) -> bool:
        return any(not isinstance(sym, Terminal) and sym.name in excluded_origins for sym in rule.expansion)

    lines: list[str] = ["root ::= ws query ws", "ws ::= [ \\t]*"]
    for origin_name, rules in grouped.items():
        if origin_name in star_bodies or origin_name in excluded_origins:
            continue
        gbnf_name = "query" if origin_name == "start" else _rule_name(origin_name)
        alternatives: list[str] = []
        for rule in rules:
            if _references_excluded(rule):
                continue
            symbols = [_symbol_gbnf(sym) for sym in rule.expansion]
            alternatives.append(" ws ".join(symbols) if symbols else '""')
        if not alternatives:
            continue
        lines.append(f"{gbnf_name} ::= " + " | ".join(alternatives))

    return "\n".join(lines) + "\n"
