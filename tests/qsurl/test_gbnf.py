"""The exported GBNF accepts every valid corpus input (spec AC16)."""
from __future__ import annotations

import re

from querysource.qsurl import to_gbnf

_CLASS_ESCAPES = {"t": "\t", "n": "\n", "r": "\r", "\\": "\\", '"': '"', "]": "]", "^": "^", "-": "-"}


def _char_in_class(ch: str, spec: str) -> bool:
    """Return True when ``ch`` is covered by a `[...]`-style class body ``spec``."""
    i = 0
    n = len(spec)
    while i < n:
        c = spec[i]
        if c == "\\":
            i += 1
            # `\t`/`\n`/... denote the real control character, not the literal
            # letter — e.g. `[ \t]` (the `ws` rule's own class) must match an
            # actual tab byte, not the letter "t".
            if _CLASS_ESCAPES.get(spec[i], spec[i]) == ch:
                return True
            i += 1
            continue
        if i + 2 < n and spec[i + 1] == "-" and spec[i + 2] != "]":
            lo, hi = spec[i], spec[i + 2]
            if lo <= ch <= hi:
                return True
            i += 3
            continue
        if c == ch:
            return True
        i += 1
    return False


class _GbnfExprParser:
    """Parse one GBNF rule's right-hand side into an Alt = list[Seq], Seq = list[Item].

    Item = (kind, quantifier, payload):
      kind "lit"   payload = literal text (already unescaped)
      kind "class" payload = (negated: bool, spec: str)
      kind "group" payload = Alt
      kind "ref"   payload = rule name

    Only needs to understand the exact subset of GBNF ``to_gbnf()`` itself emits
    (quoted literals, ``[...]``/``[^...]`` classes, ``(...)`` groups, ``|`` alternation,
    space-separated sequences, postfix ``?``/``*``/``+``) — not the full GBNF spec.
    """

    def __init__(self, text: str) -> None:
        self.t = text
        self.i = 0
        self.n = len(text)

    def parse_alt(self) -> list[list[tuple]]:
        self._skip_ws()
        branches = [self._parse_seq()]
        while self._peek() == "|":
            self.i += 1
            self._skip_ws()
            branches.append(self._parse_seq())
        return branches

    def _parse_seq(self) -> list[tuple]:
        items = []
        self._skip_ws()
        while self.i < self.n and self.t[self.i] not in "|)":
            items.append(self._parse_item())
            self._skip_ws()
        return items

    def _parse_item(self) -> tuple:
        kind, payload = self._parse_atom()
        quant = None
        if self.i < self.n and self.t[self.i] in "?*+":
            quant = self.t[self.i]
            self.i += 1
        return (kind, quant, payload)

    def _parse_atom(self) -> tuple:
        c = self.t[self.i]
        if c == '"':
            self.i += 1
            chars = []
            while self.t[self.i] != '"':
                if self.t[self.i] == "\\":
                    self.i += 1
                chars.append(self.t[self.i])
                self.i += 1
            self.i += 1
            return ("lit", "".join(chars))
        if c == "[":
            self.i += 1
            negated = False
            if self.t[self.i] == "^":
                negated = True
                self.i += 1
            start = self.i
            while self.t[self.i] != "]":
                if self.t[self.i] == "\\":
                    self.i += 1
                self.i += 1
            spec = self.t[start : self.i]
            self.i += 1
            return ("class", (negated, spec))
        if c == "(":
            self.i += 1
            alt = self.parse_alt()
            self._skip_ws()
            assert self.t[self.i] == ")", f"unbalanced group at {self.i} in {self.t!r}"
            self.i += 1
            return ("group", alt)
        start = self.i
        while self.i < self.n and (self.t[self.i].isalnum() or self.t[self.i] == "-"):
            self.i += 1
        return ("ref", self.t[start : self.i])

    def _skip_ws(self) -> None:
        while self.i < self.n and self.t[self.i] == " ":
            self.i += 1

    def _peek(self) -> str | None:
        return self.t[self.i] if self.i < self.n else None


class _GbnfMatcher:
    """Tiny backtracking recogniser for the GBNF subset to_gbnf() emits (test-only).

    Quantifiers (`?`/`*`/`+`) are matched greedily with an iterative loop (never
    recursively per repetition), so a rule matched many times in a row — e.g. the
    900+ ``&``-joined conditions in the ``eight_kb_url`` corpus case — costs one
    pass over the repeated body, not one stack frame per repetition. Nesting depth
    in this grammar is small and input-independent (query -> filter -> expr ->
    or-expr -> and-expr -> unary -> atom -> comparison -> operand -> literal), so
    ordinary recursion for grammar *structure* never approaches Python's recursion
    limit regardless of input size.
    """

    def __init__(self, grammar: str) -> None:
        self.rules: dict[str, list] = {}
        for line in grammar.splitlines():
            line = line.strip()
            if not line or "::=" not in line:
                continue
            name, _, rhs = line.partition("::=")
            self.rules[name.strip()] = _GbnfExprParser(rhs.strip()).parse_alt()

    def matches(self, text: str) -> bool:
        """Return True when the whole ``text`` derives from ``root``."""
        end = self._match_alt(self.rules["root"], text, 0)
        return end == len(text)

    def _match_alt(self, alt: list, text: str, pos: int) -> int | None:
        for seq in alt:
            end = self._match_seq(seq, text, pos)
            if end is not None:
                return end
        return None

    def _match_seq(self, seq: list, text: str, pos: int) -> int | None:
        for item in seq:
            pos = self._match_item(item, text, pos)
            if pos is None:
                return None
        return pos

    def _match_item(self, item: tuple, text: str, pos: int) -> int | None:
        kind, quant, payload = item
        if quant == "?":
            end = self._match_atom(kind, payload, text, pos)
            return end if end is not None else pos
        if quant == "*":
            while True:
                end = self._match_atom(kind, payload, text, pos)
                if end is None or end == pos:
                    return pos
                pos = end
        if quant == "+":
            end = self._match_atom(kind, payload, text, pos)
            if end is None:
                return None
            pos = end
            while True:
                end = self._match_atom(kind, payload, text, pos)
                if end is None or end == pos:
                    return pos
                pos = end
        return self._match_atom(kind, payload, text, pos)

    def _match_atom(self, kind: str, payload, text: str, pos: int) -> int | None:
        if kind == "lit":
            return pos + len(payload) if text.startswith(payload, pos) else None
        if kind == "class":
            if pos >= len(text):
                return None
            negated, spec = payload
            in_class = _char_in_class(text[pos], spec)
            return pos + 1 if (in_class != negated) else None
        if kind == "ref":
            return self._match_alt(self.rules[payload], text, pos)
        if kind == "group":
            return self._match_alt(payload, text, pos)
        raise AssertionError(f"unknown item kind {kind!r}")  # pragma: no cover


def test_gbnf_accepts_all_valid_corpus_inputs(corpus):
    matcher = _GbnfMatcher(to_gbnf())
    bad = [c["id"] for c in corpus if "ir_json" in c and not matcher.matches(c["input"])]
    assert bad == []


def test_gbnf_rejects_unknown_pipe():
    assert not _GbnfMatcher(to_gbnf()).matches("stores?state='CA':order(name)")


def test_gbnf_rule_names_are_legal():
    grammar = to_gbnf()
    pattern = re.compile(r"^[a-z0-9-]+$")
    for line in grammar.splitlines():
        if "::=" not in line:
            continue
        name = line.split("::=", 1)[0].strip()
        assert pattern.match(name), f"illegal GBNF rule name: {name!r}"
