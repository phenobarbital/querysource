---
id: F010
query_id: Q009
type: read
intent: existing startswith/contains/endswith vocabulary in the codebase (qsurl)
executed_at: 2026-10-07T00:14:00Z
duration_ms: 300
parent_id: null
depth: 0
---

# F010 — qsurl already translates `startswith`/`contains`/`endswith`/`not_contains` into `{col: {"ILIKE": pattern}}`

## Summary

`querysource/qsurl/translate.py` holds `_TEXT_PATTERNS` mapping the four textual
expressions to `(operator, prefix, suffix)` using ILIKE, and `like_escape()` which escapes
`\`, `%`, `_` before adding wildcards (quoting left to the builder). `regex` is *never*
pushed down (no provider declares the `regex` capability; it is evaluated in the residual
pandas stage, `docs/QSURL.md:92,181`). This is the precedent for semantics (case-
insensitive, metacharacter-escaped) and the escaping helper the parser-level feature can
reuse or mirror.

## Citations

- path: `querysource/qsurl/translate.py`
  lines: 12-18
  symbol: `_TEXT_PATTERNS`
  excerpt: |
    _TEXT_PATTERNS = {
        "startswith": ("ILIKE", "", "%"),
        "contains": ("ILIKE", "%", "%"),
        "endswith": ("ILIKE", "%", ""),
        "not_contains": ("NOT ILIKE", "%", "%"),
    }
- path: `querysource/qsurl/translate.py`
  lines: 21-23
  symbol: `like_escape`
  excerpt: |
    def like_escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
- path: `querysource/qsurl/translate.py`
  lines: 69-76
  symbol: `_leaf_pushdown` (text branch; regex never pushed)
  excerpt: |
    op, prefix, suffix = _TEXT_PATTERNS[expr]
    return col, {op: f"{prefix}{like_escape(value)}{suffix}"}
    # "regex" and anything unrecognised is never pushed down.
- path: `docs/QSURL.md`
  lines: 92, 179-190
  symbol: regex / ILIKE pushdown notes
- path: `querysource/qsurl/residual.py`
  lines: 23-49
  symbol: `_check_regex_safety` (`_MAX_REGEX_PATTERN_LENGTH`)
