"""Structured qsurl errors shared by the Rust and Lark back-ends."""
from __future__ import annotations

import json

from ..exceptions import QueryException  # verified: querysource/exceptions.py:6

ERROR_KINDS: tuple[str, ...] = ("parse", "lower", "unsupported", "cost")


class QSUrlError(QueryException):
    """A qsurl grammar, lowering, capability or cost error (HTTP 400).

    ``str(err)`` is the error JSON so it survives any logger; ``to_dict()`` is the
    object placed in the 400 envelope's ``detail``.
    """

    default_code: int = 400

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        offset: int = 0,
        found: str | None = None,
        expected: list[str] | None = None,
        pointer: str = "",
        code: int = 400,
    ) -> None:
        """Initialize a structured qsurl error.

        Args:
            kind: one of ``ERROR_KINDS`` ("parse", "lower", "unsupported", "cost").
            message: human-readable error message.
            offset: byte offset into the source query (parse errors only).
            found: the offending token, if known (parse errors only).
            expected: list of expected tokens (parse errors only).
            pointer: source + newline + caret pointer (parse errors only).
            code: HTTP status code; defaults to 400.

        Raises:
            ValueError: if ``kind`` is not one of ``ERROR_KINDS``.
        """
        if kind not in ERROR_KINDS:
            raise ValueError(f"unknown qsurl error kind: {kind!r}; expected one of {ERROR_KINDS}")
        super().__init__(message, code=code)
        self.kind = kind
        self.offset = offset
        self.found = found
        self.expected = list(expected or [])
        self.pointer = pointer

    @classmethod
    def from_json(cls, payload: str) -> QSUrlError:
        """Build from the JSON string the Rust binding puts in ``ValueError.args[0]``.

        Missing keys (a Rust ``lower`` error carries only kind and message) take the
        constructor defaults.

        Args:
            payload: JSON-encoded error object.

        Returns:
            The reconstructed ``QSUrlError``.
        """
        data = json.loads(payload)
        return cls(
            data["kind"],
            data["message"],
            offset=data.get("offset", 0),
            found=data.get("found"),
            expected=data.get("expected"),
            pointer=data.get("pointer", ""),
        )

    def to_dict(self) -> dict:
        """Return ``{"kind","offset","message","found","expected","pointer"}``."""
        return {
            "kind": self.kind,
            "offset": self.offset,
            "message": self.message,
            "found": self.found,
            "expected": self.expected,
            "pointer": self.pointer,
        }

    def __str__(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
