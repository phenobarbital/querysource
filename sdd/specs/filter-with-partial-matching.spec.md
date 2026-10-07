---
# SDD flow type and base branch (FEAT-145).
type: feature
base_branch: dev
projects: [parsers, rust-parsers]
tags: [where-cond, partial-matching, like, ilike, regex, postgresql, sqlserver, bigquery]
# Intentional ID reuse (see Guardrails escape hatch, same precedent as FEAT-179):
# FEAT-180 was allocated by /sdd-proposal (sdd/proposals/filter-with-partial-matching.proposal.md,
# commits b2ebe0de..2f82509b) and is already carried by sdd/state/FEAT-180/. Reserving a
# fresh ledger id (next_feature_id: 162 — the ledger lags the proposal allocator) would
# fork the feature's identity.
reuse_feature_id: FEAT-180
---

# Feature Specification: Partial-matching operators for `where_cond` / `filter` dict values

**Feature ID**: FEAT-180
**Date**: 2026-10-07
**Author**: Jesus Lara (jesuslarag@gmail.com)
**Status**: draft
**Target version**: 5.2.2

---

## 1. Motivation & Business Requirements

> Proposal: `sdd/proposals/filter-with-partial-matching.proposal.md` (accepted, all six
> open questions resolved by the requester on 2026-10-07).
> Research audit: `sdd/state/FEAT-180/`.

### Problem Statement

QuerySource consumers can only express exact equality, `IN`, NULL checks and the six
comparison tokens (`>=`, `<=`, `<>`, `!=`, `<`, `>`) inside a dict-typed `where_cond` /
`filter` value. There is no way to ask for "names that start with *andre*", "codes that
end with *-01*", "descriptions containing *pilates*" or "values matching a PostgreSQL
regular expression". The only text-pattern support today is the FEAT-152 escape hatch
(`{"city": {"ILIKE": "%san%"}}`, raw pattern, PostgreSQL only) and the legacy `field~`
key suffix (prefix ILIKE). The request:

```json
"where_cond": {
  "full_name": { "startswith": "andre" }
}
```

> we need to add a partial-matching expression in "WHERE" parser, the partial-matching
> expressions will be: startswith · endswith · contains (length no less than 3; if
> length is 1 or 2, raise an error) · regex (field will match the regex) — plus like
> and ilike.

### Goals

- G1. Accept a **partial-matching operator** as the single key of a dict-typed
  `where_cond`/`filter` value: `like`, `ilike`, `startswith`, `istartswith`, `endswith`,
  `iendswith`, `contains`, `icontains`, `regex`, `iregex`, each with a `not_` twin
  (20 operator names total) — resolved U1 / U5.
- G2. Render them on **every SQL dialect** QuerySource builds WHERE clauses for:
  PostgreSQL, generic SQL (`SQLParser`: MySQL / SQLite), SQL Server, BigQuery — in
  **both** the Cython builder and its Rust fast-path twin — resolved U2.
- G3. Case-insensitive `i*` forms render as `ILIKE` on PostgreSQL and as
  `LOWER(col) LIKE LOWER(pattern)` elsewhere — resolved U6.
- G4. `regex` family maps to PostgreSQL `~` / `~*` / `!~` / `!~*` with a bounded pattern
  length; on other dialects it raises `ParserError` — resolved U3.
- G5. `contains` family operands shorter than 3 characters raise `ParserError`
  (HTTP 400) **before** the builders run, so the error surfaces on both the Rust and the
  Cython path — resolved U4.
- G6. `startswith`/`endswith`/`contains` operands are **escaped** (`%`, `_`, escape char)
  before wildcards are added; `like`/`ilike` operands are raw patterns.
- G7. Operator semantics live in **one shared table** (Python + Rust twin) so the eight
  builders cannot drift.

### Non-Goals (explicitly out of scope)

- qsurl grammar / pushdown rules (`querysource/qsurl/translate.py` keeps emitting
  `{"ILIKE": ...}`; declaring a qsurl `regex` pushdown capability is a separate feature).
- Non-SQL dialects: CQL, Mongo, Elastic, ArangoDB, Rethink, Influx, Delta, Iceberg, SOQL.
- Regex on dialects other than PostgreSQL (BigQuery `REGEXP_CONTAINS` may follow later).
- The legacy `field~` / `field!~` key-suffix prefix match and the uppercase FEAT-152
  `ILIKE` / `NOT ILIKE` operators: both stay byte-for-byte as they are.
- Multi-operator dicts (`{"startswith": "a", "endswith": "z"}`): single-operator form only
  (the pre-dispatch hook keeps the last pair; Rust reads the first — unchanged).
- DataFrame post-filters (`querysource/types/dt/filters.py`) and `column_filter`.

---

## 2. Architectural Design

### Overview

One **operator table** is the contract. It is declared once in pure Python
(`querysource/parsers/partial_matching.py`, M1) and mirrored once in Rust
(`rust/src/partial_match.rs`, M2). Each entry fixes, for an operator name: the base kind
(`like` or `regex`), negation, case-insensitivity, the wildcard prefix/suffix, whether the
operand is escaped, and whether the `contains` minimum-length rule applies. Builders never
hard-code operator names; they look the name up and render the entry for their dialect.

The request flows through three stages:

1. **Pre-dispatch validation** (`AbstractParser._where_element`, M3). Runs inside
   `set_options()` → `set_where()`, *before* any builder and therefore before the
   pgsql/mssql/bq Rust wrappers that swallow exceptions. For a dict value whose operator
   is in the table it: requires a `str` operand, enforces `contains` ≥ 3 characters,
   caps regex patterns at 200 characters and rejects the nested-quantifier shape
   (`(x+)+`, same policy as `querysource/qsurl/residual.py:36-52`), rejects the regex
   family on parsers whose new `supports_regex_filter` flag is false, rejects a dict that
   carries a table operator together with any other key ("one operator per field" — the
   Rust builders read the first entry and this hook the last, so multi-key input would be
   path-dependent), and then **passes the raw string through untouched** (no `is_valid()` pre-quoting — the operand is quoted exactly once, by the
   builder). Failures raise `ParserError` (`default_code = 400`).
2. **Dialect rendering** (M4–M7). Each Cython builder gains a branch next to the
   FEAT-152 ILIKE branch; each Rust builder gains the same branch in
   `process_dict_value`. The branch computes the pattern (`escape(operand)` + wildcards,
   or the raw operand) and quotes it with the dialect's existing literal function.
   `jsonb_condition` (PostgreSQL) exempts the table's names from implicit JSONB
   containment, exactly as it already exempts `PG_TEXT_OPERATORS`.
3. **Defence in depth.** Values can also reach `self.filter` through `filter_options`
   (merged by `filtering_options()`, bypassing `_where_element`). Therefore every Cython
   builder re-runs the same validator (idempotent, raises `ParserError`), and every Rust
   builder returns `Err(PyValueError)` for an invalid operand. For `SQLParser` — the only
   wrapper without a Rust `try/except` — M5 adds the same swallow-and-fall-through wrapper
   that `pgSQLParser.filter_conditions` already has, so on every dialect a Rust error ends
   in the Cython path raising `ParserError`.

**Rendered forms (normative).** `E(v)` = backslash LIKE-escape (`\`→`\\`, `%`→`\%`,
`_`→`\_`, the existing qsurl `like_escape` semantics); `B(v)` = bang LIKE-escape
(`!`→`!!`, `%`→`!%`, `_`→`!_`); `Q` = the dialect's literal quoter.

| Operator | PostgreSQL (`Q` = `pg_literal`) | Generic SQL / SQL Server (`Q` = `Entity.quoteString` / `quote_string(escape_string())`) | BigQuery (`Q` = `bq_quote_string`) |
|---|---|---|---|
| `like` | `col LIKE Q(v)` | `col LIKE Q(v)` | `f LIKE Q(v)` |
| `ilike` | `col ILIKE Q(v)` | `LOWER(col) LIKE LOWER(Q(v))` | `LOWER(f) LIKE LOWER(Q(v))` |
| `startswith` | `col LIKE Q(E(v)+'%')` | `col LIKE Q(B(v)+'%') ESCAPE '!'` | `f LIKE Q(E(v)+'%')` |
| `istartswith` | `col ILIKE Q(E(v)+'%')` | `LOWER(col) LIKE LOWER(Q(B(v)+'%')) ESCAPE '!'` | `LOWER(f) LIKE LOWER(Q(E(v)+'%'))` |
| `endswith` / `iendswith` | as above with `'%'+E(v)` | as above with `'%'+B(v)` | as above |
| `contains` / `icontains` | as above with `'%'+E(v)+'%'`; operand ≥ 3 chars | as above | as above |
| `regex` | `col ~ Q(v)` | `ParserError` | `ParserError` |
| `iregex` | `col ~* Q(v)` | `ParserError` | `ParserError` |
| `not_regex` / `not_iregex` | `col !~ Q(v)` / `col !~* Q(v)` | `ParserError` | `ParserError` |
| `not_<like-op>` | `NOT LIKE` / `NOT ILIKE` in place of `LIKE` / `ILIKE` | `NOT LIKE` in place of `LIKE` (inside the `LOWER()` form too) | same |

Notes on the forms:
- PostgreSQL needs no `ESCAPE` clause: `pg_literal` emits `E'...'` with doubled
  backslashes whenever the pattern contains `\` (FEAT-152 test `code ILIKE E'a\\\\%b%'`).
- Generic SQL and SQL Server use `!` as the escape character because `\` is a
  string-literal escape in MySQL (`ESCAPE '\'` is a syntax error there) while `!` has no
  special meaning in any of their literals.
- BigQuery `LIKE` has no `ESCAPE` clause and escapes `%`/`_` with `\` natively; the
  backslash must survive `bq_quote_string` (see §8 Q1 — verified during M7).
- `f` on BigQuery is the existing `field_expr` (plain column or `JSON_VALUE(...)`).
- Operator names are matched **exactly, lowercase**. The uppercase FEAT-152 `ILIKE` /
  `NOT ILIKE` names keep their own branch (raw pattern, pre-quote strip) untouched.
- Reserved-name precedence: on PostgreSQL a dict whose single key is a table name is
  never implicit JSONB containment; on BigQuery it is never `JSON_VALUE(f, '$.<key>')`
  extraction. The reserved list is the same on every dialect and is documented in
  `docs/FILTER_OPERATORS.md` together with the JSON-key alternatives (`->>`/`@>` on
  PostgreSQL, an explicit `JSON_VALUE` field expression on BigQuery).
- Escaping ownership is per operator and fixed by the table's `escape` flag: `like` /
  `ilike` / `regex` families receive a **ready pattern**; `startswith` / `endswith` /
  `contains` families receive a **raw value** that the builder escapes. qsurl keeps
  emitting the uppercase FEAT-152 `ILIKE` operator with an already-escaped pattern, so
  qsurl input never enters the new branch and cannot be double-escaped.
- Vocabulary note (design-research S1, escalated as §8 Q3): qsurl's URL expression
  `startswith` is translated to a case-insensitive `ILIKE` dict, whereas the new
  `where_cond` operator `startswith` is case-sensitive per U1. The two never meet inside
  the parser, but the words differ in meaning across the two entry points.

### Component Diagram
```
request where_cond ──► AbstractParser.set_where ──► _where_element (M3)
                                                        │ op in PARTIAL_MATCH_OPERATORS?
                                                        │   validate_partial_match()  ─► ParserError (400)
                                                        │   supports_regex_filter?
                                                        ▼ raw str passes through
                     partial_matching.py (M1) ◄── lookup ── dialect builder.filter_conditions
                     partial_match.rs   (M2) ◄── lookup ──┤
                                                        ├─ pgSQLParser  ─► _rs.pgsql_filter_conditions │ _filter_conditions_cy   (M4)
                                                        ├─ SQLParser    ─► _rs.filter_conditions       │ Cython fallback          (M5)
                                                        ├─ msSQLParser  ─► _rs.mssql_filter_conditions │ _filter_conditions_cy   (M6)
                                                        └─ BigQueryParser► _rs.bq_filter_conditions    │ _filter_conditions_cy   (M7)
                                        Rust Err(PyValueError) ──swallowed──► Cython path ──► ParserError
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `AbstractParser._where_element` (`abstract.pyx:564`) | modifies | validation + raw pass-through for table operators |
| `AbstractParser` struct (`abstract.pxd`) | extends | new `cdef public bint supports_regex_filter` (default False) |
| `pgSQLParser` (`pgsql.pyx:298`) | modifies | sets `supports_regex_filter = True`; new dict branch; `jsonb_condition` exemption |
| `PG_TEXT_OPERATORS` (`pgsql.pyx:36`, `pgsql_parser.rs:90`) | untouched | FEAT-152 branch kept; new ops use their own allowlist |
| `SQLParser.filter_conditions` (`sql.pyx:113`) | modifies | Rust swallow-and-fall-through wrapper + dict branch |
| `msSQLParser._filter_conditions_cy` (`sqlserver.pyx:80`) | extends | first dict-value branch in this parser |
| `filter_common.rs::FilterValue` (`filter_common.rs:20`) | extends | new `Dict(Vec<(String, FilterValue)>)` variant; `process_entry` keeps returning `None` for it (SOQL unaffected); mssql handles it |
| `BigQueryParser._filter_conditions_cy` (`bigquery.pyx:209`) | modifies | table names take precedence over `JSON_VALUE` key extraction |
| `querysource.exceptions.ParserError` (`exceptions.py:86`) | uses | the single error type (HTTP 400) |
| `querysource/qsurl/translate.py::like_escape` (`translate.py:21`) | mirrored, not imported | M1 re-declares the same semantics; qsurl untouched (non-goal) |

### Data Models
```python
# querysource/parsers/partial_matching.py (M1) — plain dataclass, no Pydantic (hot path, imported by Cython)
@dataclass(frozen=True, slots=True)
class PartialMatchOp:
    name: str            # "istartswith"
    kind: str            # "like" | "regex"
    negated: bool        # not_* twin
    insensitive: bool    # i* form → ILIKE / LOWER()
    prefix: str          # "" | "%"
    suffix: str          # "" | "%"
    escape: bool         # True for startswith/endswith/contains families, False for like/ilike/regex
    min_length: int      # 3 for the contains family, 0 otherwise
```

### New Public Interfaces
```python
# querysource/parsers/partial_matching.py
PARTIAL_MATCH_OPERATORS: dict[str, PartialMatchOp]   # exactly 20 entries
CONTAINS_MIN_LENGTH: int = 3
MAX_REGEX_PATTERN_LENGTH: int = 200                   # mirrors querysource/qsurl/residual.py:35
LIKE_ESCAPE_CHAR_BANG: str = "!"

def like_escape(value: str) -> str: ...
def like_escape_bang(value: str) -> str: ...
def validate_partial_match(key: str, op: str, value: object, *, supports_regex: bool) -> PartialMatchOp: ...
def build_like_pattern(op: PartialMatchOp, value: str, *, escaper: Callable[[str], str]) -> str: ...
```
No new HTTP endpoints, no new request fields: the operators are new dict keys inside the
existing `where_cond` / `filter` options.

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: Python operator table + validator | yes | 20 names fixed in §2; signatures fixed in §3; `ParserError` messages fixed below | — |
| M2: Rust twin | yes | exact twin of M1 (`PartialMatchOp` struct, `lookup`, `like_escape`, `like_escape_bang`, `build_like_pattern`, `validate`) | — |
| M3: Pre-dispatch validation | yes | edit site `abstract.pyx:572`; flag in `abstract.pxd`; raw pass-through rule fixed | — |
| M4: PostgreSQL builders | yes | rendered forms table §2; anchors §6; JSONB exemption mirrors FEAT-152 | — |
| M5: Generic SQL builders | yes | rendered forms table §2; `ESCAPE '!'`; add Rust swallow wrapper | — |
| M6: SQL Server builders | yes | `FilterValue::Dict` variant; mssql-only `process_mssql_entry`; Cython dict branch | — |
| M7: BigQuery builders | yes, after §8 Q1 is answered by reading `bq_quote_string` | precedence rule over `JSON_VALUE`; forms §2 | — |
| M8: Tests | yes | file names, case corpus and both-path harness fixed in §4 | — |
| M9: Docs + version bump | yes | `docs/FILTER_OPERATORS.md`; `version.py:9` → `5.2.2` | — |

### Module 1: Python operator table, escaping helpers and validator
- **Path**: `querysource/parsers/partial_matching.py` (new, pure Python so Cython
  modules and tests import the same object)
- **Responsibility**: single source of truth for operator semantics; the validator
  raised pre-dispatch (M3) and re-run by every Cython builder (M4–M7).
- **Depends on**: `querysource.exceptions.ParserError` (`exceptions.py:86`).
- **Interface Skeleton** *(signatures + docstrings only — bodies belong to task blueprints, FEAT-545)*:
  ```python
  # querysource/parsers/partial_matching.py  (new)
  from __future__ import annotations
  import re
  from dataclasses import dataclass
  from typing import Callable
  from ..exceptions import ParserError  # verified: querysource/exceptions.py:86

  CONTAINS_MIN_LENGTH: int = 3
  MAX_REGEX_PATTERN_LENGTH: int = 200          # same bound as querysource/qsurl/residual.py:35
  LIKE_ESCAPE_CHAR_BANG: str = "!"

  @dataclass(frozen=True, slots=True)
  class PartialMatchOp:
      """One row of the operator table (see spec §2 Data Models)."""
      name: str; kind: str; negated: bool; insensitive: bool
      prefix: str; suffix: str; escape: bool; min_length: int

  PARTIAL_MATCH_OPERATORS: dict[str, PartialMatchOp]
  """Exactly 20 entries: like, ilike, startswith, istartswith, endswith, iendswith,
  contains, icontains, regex, iregex and their not_ twins. Keys are lowercase."""

  def like_escape(value: str) -> str:
      """Escape ``\\``, ``%`` and ``_`` with a backslash (PostgreSQL / BigQuery patterns)."""

  def like_escape_bang(value: str) -> str:
      """Escape ``!``, ``%`` and ``_`` with ``!`` (generic SQL / SQL Server, paired with ESCAPE '!')."""

  def build_like_pattern(op: PartialMatchOp, value: str, *, escaper: Callable[[str], str]) -> str:
      """Return ``prefix + (escaper(value) if op.escape else value) + suffix``. Never quotes."""

  def validate_partial_match(key: str, op: str, value: object, *, supports_regex: bool) -> PartialMatchOp:
      """Validate an operand for a table operator and return its entry.

      Raises:
          ParserError: ``op`` not in the table; ``value`` is not a ``str``;
              ``len(value) < op.min_length`` ("contains on '<key>' requires at least 3
              characters (got N)"); ``op.kind == 'regex'`` and ``not supports_regex``
              ("regex operators are not supported by this query parser"); regex pattern
              empty, longer than MAX_REGEX_PATTERN_LENGTH, or matching
              NESTED_QUANTIFIER_RE (the residual.py:36 policy, mirrored here).
      """

  NESTED_QUANTIFIER_RE: re.Pattern[str]   # re.compile(r"\([^()]*[+*][^()]*\)[+*]"), mirrors querysource/qsurl/residual.py:36

  def validate_partial_match_dict(key: str, value: dict, *, supports_regex: bool) -> PartialMatchOp | None:
      """Entry point for AbstractParser._where_element and the Cython builders.

      Returns None when no key of ``value`` is a table operator (caller keeps today's
      behaviour). Raises ParserError("one operator per field: '<key>'") when a table
      operator is combined with any other key; otherwise delegates to
      validate_partial_match for the single (op, operand) pair.
      """
  ```

### Module 2: Rust twin of the operator table
- **Path**: `rust/src/partial_match.rs` (new) + one `mod partial_match;` line in
  `rust/src/lib.rs` (no new `#[pyfunction]`; the module is internal).
- **Responsibility**: identical table, escaping and validation for the four Rust builders.
- **Depends on**: nothing new (`pyo3::exceptions::PyValueError` for errors).
- **Interface Skeleton**:
  ```rust
  // rust/src/partial_match.rs  (new)
  pub const CONTAINS_MIN_LENGTH: usize = 3;
  pub const MAX_REGEX_PATTERN_LENGTH: usize = 200;

  #[derive(Debug, Clone, Copy, PartialEq, Eq)]
  pub enum MatchKind { Like, Regex }

  #[derive(Debug, Clone, Copy)]
  pub struct PartialMatchOp {
      pub name: &'static str, pub kind: MatchKind, pub negated: bool, pub insensitive: bool,
      pub prefix: &'static str, pub suffix: &'static str, pub escape: bool, pub min_length: usize,
  }

  pub const PARTIAL_MATCH_OPERATORS: &[PartialMatchOp];   // 20 entries, same order as M1
  pub fn lookup(op: &str) -> Option<&'static PartialMatchOp>;
  pub fn like_escape(value: &str) -> String;               // twin of M1 like_escape
  pub fn like_escape_bang(value: &str) -> String;          // twin of M1 like_escape_bang
  pub fn build_like_pattern(op: &PartialMatchOp, value: &str, escaper: fn(&str) -> String) -> String;
  /// Twin of M1 validate_partial_match; Err carries the same message text.
  pub fn validate(key: &str, op: &PartialMatchOp, value: &str, supports_regex: bool) -> PyResult<()>;
  //   twin of M1 validate_partial_match incl. the nested-quantifier regex check;
  //   non-string operands and multi-key dicts are rejected by the caller with the same messages
  #[cfg(test)] mod tests { /* table has 20 names; escape helpers; validate errors */ }
  ```

### Module 3: Pre-dispatch validation in `AbstractParser`
- **Path**: `querysource/parsers/abstract.pyx` (modify `_where_element`, lines 567–574) +
  `querysource/parsers/abstract.pxd` (add the flag).
- **Responsibility**: raise `ParserError` before any builder runs; pass the raw operand
  through without `is_valid()` pre-quoting for table operators only.
- **Depends on**: M1.
- **Interface Skeleton**:
  ```cython
  # querysource/parsers/abstract.pxd  (modifies abstract.pxd:48 — after `cdef public bint string_literal`)
  cdef public bint supports_regex_filter   # False by default; pgSQLParser sets True

  # querysource/parsers/abstract.pyx  (modifies abstract.pyx:19 imports, :69 set_attributes, :564 _where_element)
  from .partial_matching import PARTIAL_MATCH_OPERATORS, validate_partial_match   # new import

  cdef void set_attributes(self):            # verified: abstract.pyx:69
      ...; self.supports_regex_filter = False

  async def _where_element(self, key, value, connection):   # verified: abstract.pyx:564
      """...existing docstring + :
      Dict values whose operator is a partial-matching operator are validated
      (validate_partial_match) and returned as {op: raw_str} — no is_valid() pre-quoting;
      the dialect builder quotes the operand exactly once."""
  ```

### Module 4: PostgreSQL builders (Cython + Rust)
- **Path**: `querysource/parsers/pgsql.pyx`, `rust/src/pgsql_parser.rs`
- **Responsibility**: render the table on PostgreSQL (`LIKE`/`ILIKE`/`~` family);
  exempt table names in `jsonb_condition`; set `supports_regex_filter = True`.
- **Depends on**: M1 (Cython), M2 (Rust), M3 (flag).
- **Interface Skeleton**:
  ```cython
  # querysource/parsers/pgsql.pyx
  from .partial_matching import PARTIAL_MATCH_OPERATORS, validate_partial_match, build_like_pattern, like_escape  # new import

  cdef str partial_match_condition(str col, object op, str operand):
      """Render one table operator for PostgreSQL per spec §2 rendered forms.
      Quotes via pg_literal (verified: pgsql.pyx:41). Raises ParserError via
      validate_partial_match(supports_regex=True)."""

  cdef tuple jsonb_condition(str col, dict value):      # verified: pgsql.pyx:234
      # new early return (False, None) when `op in PARTIAL_MATCH_OPERATORS`, next to pgsql.pyx:270

  cdef class pgSQLParser(SQLParser):                     # verified: pgsql.pyx:298
      def __init__(self, *args, **kwargs): ...; self.supports_regex_filter = True
      async def _filter_conditions_cy(self, sql):        # verified: pgsql.pyx:315
          # new `elif op in PARTIAL_MATCH_OPERATORS:` branch after pgsql.pyx:372
  ```
  ```rust
  // rust/src/pgsql_parser.rs
  use crate::partial_match::{lookup, like_escape, build_like_pattern, validate};
  fn pg_validate_operator(op: &str) -> bool            // verified: pgsql_parser.rs:37 — add `|| lookup(op).is_some()`
  fn jsonb_condition(...) -> JsonbOutcome              // verified: pgsql_parser.rs:343 — `lookup(op).is_some()` ⇒ NotJsonb (next to :361)
  fn pg_partial_match_condition(key: &str, op: &PartialMatchOp, operand: &str) -> PyResult<String>;
  fn process_dict_value(...) -> Option<String>          // verified: pgsql_parser.rs:474 — becomes PyResult<Option<String>> or
                                                         // keeps Option and the caller surfaces Err; decision: return PyResult<Option<String>>
  ```

### Module 5: Generic SQL builders (Cython + Rust)
- **Path**: `querysource/parsers/sql.pyx`, `rust/src/sql_parser.rs`
- **Responsibility**: `LIKE` / `LOWER()` forms with `ESCAPE '!'`; regex ⇒ `ParserError`;
  add the Rust swallow-and-fall-through wrapper (same shape as `pgsql.pyx:305-313`).
- **Depends on**: M1, M2, M3.
- **Interface Skeleton**:
  ```cython
  # querysource/parsers/sql.pyx
  from .partial_matching import PARTIAL_MATCH_OPERATORS, validate_partial_match, build_like_pattern, like_escape_bang  # new import

  cdef str sql_partial_match_condition(str col, object op, str operand):
      """Render one table operator for generic SQL per spec §2 (ESCAPE '!'); quotes via
      Entity.quoteString (verified: sql.pyx:14 import). Raises ParserError."""

  cdef class SQLParser(AbstractParser):                  # verified: sql.pyx:85
      async def filter_conditions(self, sql):            # verified: sql.pyx:113
          """Rust fast path wrapped in try/except Exception → Cython fallback (mirrors pgsql.pyx:305)."""
          # new `elif op in PARTIAL_MATCH_OPERATORS:` branch replacing the discard at sql.pyx:161-163
  ```
  ```rust
  // rust/src/sql_parser.rs
  use crate::partial_match::{lookup, like_escape_bang, build_like_pattern, validate};
  fn validate_operator(op: &str) -> Result<(), String>  // verified: sql_parser.rs:47 — accept `lookup(op).is_some()`
  fn sql_partial_match_condition(key: &str, op: &PartialMatchOp, operand: &str) -> PyResult<String>;
  pub fn filter_conditions(...)                          // verified: sql_parser.rs:218 — dict branch at :246 renders table ops; regex ⇒ Err
  ```

### Module 6: SQL Server builders (Cython + Rust)
- **Path**: `querysource/parsers/sqlserver.pyx`, `rust/src/mssql_parser.rs`,
  `rust/src/filter_common.rs`
- **Responsibility**: first dict-value branch for SQL Server, limited to table operators
  (other dict operators keep being skipped — no comparison-token support is added here).
- **Depends on**: M1, M2, M3.
- **Interface Skeleton**:
  ```cython
  # querysource/parsers/sqlserver.pyx
  from .partial_matching import PARTIAL_MATCH_OPERATORS, validate_partial_match, build_like_pattern, like_escape_bang  # new import
  cdef str mssql_partial_match_condition(str col, object op, str operand):
      """Same forms as M5 (T-SQL accepts LOWER(), LIKE ... ESCAPE '!'); quotes via Entity.quoteString."""
  cdef class msSQLParser(SQLParser):                     # verified: sqlserver.pyx:22
      async def _filter_conditions_cy(self, sql):        # verified: sqlserver.pyx:80
          # new `if isinstance(value, dict):` branch inserted before sqlserver.pyx:114
  ```
  ```rust
  // rust/src/filter_common.rs
  pub enum FilterValue { Str, Int, Float, Bool, List(Vec<FilterValue>), Dict(Vec<(String, FilterValue)>), Null }  // verified: filter_common.rs:20
  pub fn extract_filter_value(...)                       // verified: filter_common.rs:55 — cast::<PyDict> before the list extraction
  pub fn process_entry(entry: &FilterEntry) -> Option<String>  // verified: filter_common.rs:110 — `FilterValue::Dict(_) => None` (SOQL unaffected)
  // rust/src/mssql_parser.rs
  fn process_mssql_entry(entry: &FilterEntry) -> PyResult<Option<String>>;  // Dict ⇒ table ops, else delegate to process_entry
  pub fn mssql_filter_conditions(...)                    // verified: mssql_parser.rs:23 — uses process_mssql_entry
  ```

### Module 7: BigQuery builders (Cython + Rust)
- **Path**: `querysource/parsers/bigquery.pyx`, `rust/src/bigquery_parser.rs`
- **Responsibility**: render the table on BigQuery with `field_expr`; table names take
  precedence over the existing `JSON_VALUE(f, '$.<key>')` extraction.
- **Depends on**: M1, M2, M3; §8 Q1 (backslash handling in `bq_quote_string`).
- **Interface Skeleton**:
  ```cython
  # querysource/parsers/bigquery.pyx
  from .partial_matching import PARTIAL_MATCH_OPERATORS, validate_partial_match, build_like_pattern, like_escape  # new import
  cdef str bq_partial_match_condition(str field_expr, object op, str operand):
      """Forms per §2 BigQuery column; quotes via bq_quote_string (verified: bigquery.pyx:31)."""
  cdef class BigQueryParser(SQLParser):
      async def _filter_conditions_cy(self, sql):        # verified: bigquery.pyx:139
          # new `elif op in PARTIAL_MATCH_OPERATORS:` between bigquery.pyx:213 and the JSON_VALUE else-branch
  ```
  ```rust
  // rust/src/bigquery_parser.rs
  fn process_dict_value(field_expr: &str, entries: &[(String, FilterValue)]) -> Option<String>  // verified: bigquery_parser.rs:181 — table lookup before the JSON_VALUE fallback
  ```

### Module 8: Dual-path tests
- **Path**: `tests/test_partial_matching_operators.py` (M1), `tests/test_pgsql_partial_matching.py`,
  `tests/test_sql_partial_matching.py`, `tests/test_mssql_partial_matching.py`,
  `tests/test_bigquery_partial_matching.py`, `tests/test_partial_matching_prevalidation.py`
  (all new) + Rust `#[cfg(test)]` blocks in M2/M4–M7.
- **Responsibility**: the §4 corpus on both paths with the FEAT-152 stale-extension skip
  guard; full-flow `set_where()` error tests.
- **Depends on**: M1–M7.
- **Interface Skeleton**: see §4 (harness copied from `tests/qsurl/test_pg_ilike.py:1-60`).

### Module 9: Documentation and version bump
- **Path**: `docs/FILTER_OPERATORS.md` (new), `querysource/version.py:9`
- **Responsibility**: operator table, per-dialect rendering, reserved-name precedence,
  error messages, examples; link from `docs/QSURL.md` text_match section is a non-goal
  (qsurl untouched). Version `5.2.1` → `5.2.2`.
- **Depends on**: none.

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_table_has_twenty_lowercase_names` | M1 | `set(PARTIAL_MATCH_OPERATORS) == {like, ilike, startswith, istartswith, endswith, iendswith, contains, icontains, regex, iregex} ∪ not_*` |
| `test_like_escape_backslash_percent_underscore` | M1 | `like_escape("a\\%_b") == "a\\\\\\%\\_b"`; `like_escape_bang("a!%_b") == "a!!!%!_b"` |
| `test_build_like_pattern_forms` | M1 | startswith/endswith/contains produce `v%`, `%v`, `%v%` with escaping; like/regex untouched |
| `test_validate_contains_min_length` | M1 | `"ab"` → `ParserError` with the fixed message; `"abc"` passes; applies to icontains/not_contains/not_icontains |
| `test_validate_non_string_operand` | M1 | int/list/dict operands → `ParserError` for every operator |
| `test_validate_regex_bounds_and_support` | M1 | pattern `""`, > 200 chars and nested-quantifier shapes `(a+)+`, `(a*)+` → `ParserError`; `supports_regex=False` → `ParserError`; valid pattern passes |
| `test_validate_dict_rejects_multi_key` | M1 | `{"startswith": "a", "endswith": "z"}` and `{"contains": "abc", ">=": 1}` → `ParserError("one operator per field")`; `{">=": 1, "<": 5}` → `None` (not ours) |
| `test_like_escape_bang_escapes_bracket` | M1 | `like_escape_bang("a[b]") == "a![b]"` (§8 Q2 default) |
| `test_shared_process_entry_ignores_dict` | M6 | `filter_common::process_entry` returns `None` for `FilterValue::Dict`; SOQL and CQL builders render the same SQL as before for a dict-valued filter (`cargo test`) |
| `test_where_element_passthrough_raw` | M3 | `await parser.set_where({"n": {"startswith": "o'brien"}}, None)` leaves `parser.filter == {"n": {"startswith": "o'brien"}}` (no pre-quote) |
| `test_where_element_keeps_is_valid_for_other_dicts` | M3 | `{"n": {">=": "5"}}` still goes through `is_valid` (regression guard for FEAT-152 strip logic) |
| `test_pg_rendering[path, op]` | M4 | the §2 PostgreSQL column for all 20 operators, rust and cython |
| `test_pg_escaping_corpus[path]` | M4 | operands `o'brien`, `50%`, `a_b`, `a\b`, `{json}` render per `pg_literal` (E'' form for backslashes/braces) |
| `test_pg_jsonb_exemption[path]` | M4 | `{"meta": {"contains": "abc"}}` renders `meta LIKE '%abc%'`, never `meta @> ...::jsonb` |
| `test_pg_legacy_ilike_and_suffix_untouched[path]` | M4 | `{"city": {"ILIKE": "'%san%'"}}` and `{"name~": "'ab'"}` render exactly as before this feature |
| `test_sql_rendering[path, op]` | M5 | generic column incl. `ESCAPE '!'`; regex ops ⇒ `ParserError` (cython) / `Err` swallowed ⇒ `ParserError` (full `filter_conditions`) |
| `test_sql_rust_error_falls_through` | M5 | with `HAS_RUST` true, an invalid operand through `SQLParser.filter_conditions` raises `ParserError`, not `ValueError` |
| `test_mssql_rendering[path, op]` | M6 | T-SQL column; unknown dict operator still skipped; SOQL `process_entry` unchanged for `Dict` |
| `test_bq_rendering[path, op]` | M7 | BigQuery column with plain and `JSON_VALUE` `field_expr`; `{"f": {"contains": "abc"}}` is LIKE, `{"f": {"other": "x"}}` is still `JSON_VALUE(f, '$.other') = 'x'` |
| `test_builders_agree_on_every_case` (per dialect) | M4–M7 | rust vs cython `_where_body` equality for the full corpus |
| `cargo test -p qs_parsers partial_match` | M2 | table size, escape helpers, validate errors, each dialect's rendering function |

### Integration Tests
| Test | Description |
|---|---|
| `test_set_where_raises_before_builder[dialect, use_rust]` | `await parser.set_where({"n": {"contains": "ab"}}, None)` raises `ParserError` on `pgSQLParser`, `SQLParser`, `msSQLParser`, `BigQueryParser` with `HAS_RUST` monkeypatched both ways |
| `test_set_where_regex_requires_postgres` | `{"n": {"regex": "^a"}}` passes `set_where` on `pgSQLParser`, raises `ParserError` on the other three |
| `test_filter_options_path_still_validates` | operand injected via `filter_options` (bypassing `set_where`) still raises `ParserError` from `filter_conditions` on every dialect |
| `test_build_query_sqlglot_valid[dialect, op]` | full `build_query()` output parses with `sqlglot` for the dialect (pattern from `tests/test_sql_parser_combinations.py`) |
| `test_build_query_pg_regex_grouping` | `~`, `~*`, `!~`, `!~*` conditions combined with a scalar filter AND-join correctly |
| `test_conformance_matrix[dialect, path, op, operand_kind]` | parametrized over 4 dialects × 2 paths × 20 operators × operand kinds {plain, wildcard chars, quote, backslash, pre-quoted-looking, non-str, too-short, multi-key}: expected SQL body or expected `ParserError` message |
| `test_proposal_example_end_to_end` | `{"full_name": {"startswith": "andre"}}` on `pgSQLParser` renders `full_name LIKE 'andre%'` through `set_where` + `build_query` |

### Test Data / Fixtures
```python
# tests/test_pgsql_partial_matching.py — harness copied from tests/qsurl/test_pg_ilike.py:1-60
SQL = "SELECT * FROM t {where_cond}"

def _rust_supports_partial_match() -> bool:
    if not pgsql.HAS_RUST:
        return False
    rendered = pgsql._rs.pgsql_filter_conditions(SQL, {"n": {"startswith": "andre"}}, {})
    return "n LIKE 'andre%'" in rendered

PATHS = [pytest.param("rust", marks=pytest.mark.skipif(not RUST_AVAILABLE, reason="stale _qs_parsers; run make build-rust && make stage-rust")), "cython"]

OPERATOR_CORPUS = [  # (filter, expected WHERE body on PostgreSQL)
    ({"n": {"startswith": "andre"}}, "n LIKE 'andre%'"),
    ({"n": {"istartswith": "andre"}}, "n ILIKE 'andre%'"),
    ({"n": {"not_endswith": "01"}}, "n NOT LIKE '%01'"),
    ({"n": {"icontains": "pilates"}}, "n ILIKE '%pilates%'"),
    ({"n": {"like": "an_re%"}}, "n LIKE 'an_re%'"),          # raw pattern, no escaping
    ({"n": {"contains": "50%"}}, "n LIKE E'%50\\\\%%'"),       # escaped wildcard
    ({"n": {"regex": "^an.*e$"}}, "n ~ '^an.*e$'"),
    ({"n": {"not_iregex": "^an"}}, "n !~* '^an'"),
]
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] AC1. `PARTIAL_MATCH_OPERATORS` (Python) and `PARTIAL_MATCH_OPERATORS` (Rust) each hold exactly the 20 lowercase names listed in §1 G1, in the same order, with identical per-entry fields (`cargo test` + `pytest tests/test_partial_matching_operators.py`).
- [ ] AC2. `{"full_name": {"startswith": "andre"}}` on `pgSQLParser` renders `full_name LIKE 'andre%'` on both the Rust and the Cython path, through `set_where()` + `build_query()`.
- [ ] AC3. Every row of the §2 rendered-forms table is asserted by a test on both paths for PostgreSQL, generic SQL, SQL Server and BigQuery (`tests/test_*_partial_matching.py` all green).
- [ ] AC4. `contains` / `icontains` / `not_contains` / `not_icontains` with an operand of length 1 or 2 raises `ParserError` (code 400) from `set_where()` on all four parsers with `HAS_RUST` both true and false; `startswith`, `endswith`, `like` accept any length.
- [ ] AC5. `regex` / `iregex` / `not_regex` / `not_iregex` render `~` / `~*` / `!~` / `!~*` on `pgSQLParser` and raise `ParserError` from `set_where()` on `SQLParser`, `msSQLParser`, `BigQueryParser`; a pattern that is empty, longer than 200 characters, or matches the nested-quantifier shape of `querysource/qsurl/residual.py:36` raises `ParserError` everywhere.
- [ ] AC6. A non-string operand for any table operator raises `ParserError` from `set_where()`; an operand injected through `filter_options` is still rejected by `filter_conditions()` on every dialect.
- [ ] AC7. `startswith` / `endswith` / `contains` operands containing `%`, `_`, `\` or `!` never act as wildcards (escaping corpus green on all dialects; generic SQL / SQL Server emit `ESCAPE '!'`).
- [ ] AC8. On PostgreSQL `{"col": {"<table-op>": "..."}}` is never rendered as JSONB containment; on BigQuery it is never rendered as `JSON_VALUE(col, '$.<table-op>')`; all other dict keys keep today's behaviour (existing `tests/test_pgsql_jsonb_filters.py`, `tests/qsurl/test_pg_ilike.py`, `tests/test_rust_parsers.py`, `tests/test_sql_parser_combinations.py` stay green).
- [ ] AC9. The uppercase `ILIKE` / `NOT ILIKE` operators and the `field~` / `field!~` suffix forms render byte-identically to the pre-feature output.
- [ ] AC10. With `HAS_RUST` true, a Rust-side validation error on any dialect (including `SQLParser`) surfaces as `ParserError`, never as a raw `ValueError`/`PyValueError`.
- [ ] AC11. `cargo test --manifest-path rust/Cargo.toml` and `make build-rust && make stage-rust && pytest tests/ -q` pass; `ruff check querysource/parsers/partial_matching.py tests/` is clean.
- [ ] AC12. `docs/FILTER_OPERATORS.md` documents the 20 operators, per-dialect rendering, the reserved-name precedence and the error messages; `querysource/version.py` is `5.2.2`.
- [ ] AC13. No change to `querysource/qsurl/` and to the SOQL/CQL/Mongo/Elastic/Arango/Rethink builders (`git diff --stat` on those paths is empty); `filter_common::process_entry` returns `None` for the new `Dict` variant so SOQL/CQL output is unchanged.
- [ ] AC14. A dict that combines a table operator with any other key (`{"startswith": "a", "endswith": "z"}`, `{"contains": "abc", ">=": 1}`) raises `ParserError("one operator per field ...")` from `set_where()` and from every Cython builder; dicts without a table operator keep today's first/last-key behaviour untouched.

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> This section is the single source of truth for what exists in the codebase.
> Implementation agents MUST NOT reference imports, attributes, or methods
> not listed here without first verifying they exist via `grep` or `read`.
> Re-verified against commit `2f82509b` on 2026-10-07 (the proposal's findings
> `sdd/state/FEAT-180/findings/F001-F014.md` were re-read; all anchors still hold).

### Verified Imports
```python
from ..exceptions import ParserError                      # verified: querysource/exceptions.py:86 (class ParserError(QueryException): default_code = 400)
from ..exceptions import EmptySentence                    # verified: querysource/parsers/sql.pyx:13 (existing import style inside parsers)
from ..types.validators import Entity, is_valid, field_components  # verified: querysource/parsers/abstract.pyx:19
from ..types.validators import Entity, field_components   # verified: querysource/parsers/sql.pyx:14, sqlserver.pyx:11
from . import QS_FILTERS, QS_VARIABLES                    # verified: querysource/parsers/abstract.pyx:14 (plain .py module import from a .pyx works)
from querysource.qs_parsers import _qs_parsers as _rs     # verified: pgsql.pyx:21, sql.pyx:19, sqlserver.pyx:17 (guarded by HAS_RUST)
from querysource.parsers import pgsql                     # verified: tests/qsurl/test_pg_ilike.py:7
from querysource.parsers.pgsql import pgSQLParser         # verified: tests/qsurl/test_pg_ilike.py:8
from querysource.parsers.sql import SQLParser             # verified: tests/test_sql_parser_combinations.py:20
from querysource.models import QueryObject                # verified: tests/qsurl/test_pg_ilike.py:6
from querysource import qs_parsers                        # verified: tests/test_rust_parsers.py:9
```
```rust
use crate::validators::{escape_string, field_components, quote_string};  // verified: rust/src/sql_parser.rs:11, filter_common.rs:12
use crate::filter_common::{apply_where_clause, extract_entries, process_entry};  // verified: rust/src/mssql_parser.rs:10
use pyo3::types::{PyAny, PyDict, PyList, PyString};       // verified: rust/src/pgsql_parser.rs:9
```

### Existing Class Signatures
```cython
# querysource/parsers/abstract.pxd
cdef class AbstractParser:
    cdef public dict filter                      # line 17
    cdef public dict cond_definition             # line 30
    cdef public bint string_literal              # line 48  (noquote flag passed to is_valid)
    cdef void set_attributes(self)               # line 53  (pyx: abstract.pyx:69, sets defaults)
    cpdef object where_cond(self, dict where)    # line 56

# querysource/parsers/abstract.pyx
cdef tuple START_TOKENS = ('@', '$', '~', '^', '?', '*')                      # line 23
cdef tuple END_TOKENS = ('|', '&', '!', '<', '>')                              # line 24
cdef tuple KEYWORD_TOKENS = ('::', '@>', '<@', '->', '->>', '>=', '<=', '<>', '!=', '<', '>')  # line 25
cdef class AbstractParser:                                                     # line 28
    cdef void _query_filter_sync(self)           # line 308: pops 'where_cond' then 'filter' into self.filter
    cpdef str filtering_options(self, str sentence)  # line 450: merges self.filter_options INTO self.filter (bypasses _where_element)
    async def _where_element(self, key, value, connection)  # line 564; dict branch lines 567-574:
    #     op, v = next(reversed(value.items()))                                 # line 572
    #     result = is_valid(key, v, noquote=self.string_literal)                 # line 573
    #     return key, {op: result}
    async def set_where(self, _filter: dict, connection: object) -> object      # line 602 (gather of _where_element; connection may be None)

# querysource/parsers/sql.pxd / sql.pyx
cdef class SQLParser(AbstractParser):                                          # sql.pxd:7, sql.pyx:85
    cdef public object valid_operators            # sql.pxd:9; set to ('<','>','>=','<=','<>','!=','IS NOT','IS') at sql.pyx:97
    async def filter_conditions(self, sql)        # sql.pyx:113; Rust fast path at :118-119 WITHOUT try/except
    #   dict branch sql.pyx:152-163: `if op in COMPARISON_TOKENS:` (:157) else `continue` (:163)
COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)                        # sql.pyx:25 (same literal at pgsql.pyx:27, bigquery.pyx:28)

# querysource/parsers/pgsql.pyx
JSONB_OPERATORS = ('@>', '<@', '@>|', '@!', '@$', '->', '->>',)                # line 31
JSONB_KEY_SUFFIXES = '|!~#@:'                                                   # line 34
PG_TEXT_OPERATORS = ('ILIKE', 'NOT ILIKE',)                                     # line 36
cdef str pg_literal(str value)                                                 # line 41 (doubles '; E'' form with \x7b/\x7d when { } or \ present)
cdef tuple jsonb_condition(str col, dict value)                                # line 234; PG_TEXT_OPERATORS early-return at line 270
cdef class pgSQLParser(SQLParser):                                             # line 298
    async def filter_conditions(self, sql)        # line 305; Rust call :310 inside `try: ... except Exception: pass` (:311-312)
    async def _filter_conditions_cy(self, sql)    # line 315; dict branch :359-400; ILIKE branch `elif op in PG_TEXT_OPERATORS and isinstance(v, str):` (:372)
    #   str branch: `if end == '~':` → ILIKE prefix (:444-451)

# querysource/parsers/sqlserver.pyx
cdef class msSQLParser(SQLParser):                                             # line 22 (pxd: sqlserver.pxd:5, `cdef bint _procedure`)
    async def filter_conditions(self, sql)        # line 69; Rust call inside try/except Exception (:73-78)
    async def _filter_conditions_cy(self, sql)    # line 80; value dispatch starts `if isinstance(value, list):` (:114) — NO dict branch exists
    #   `elif isinstance(value, (str, int)):` (:134)

# querysource/parsers/bigquery.pyx
cdef str bq_quote_string(object value)                                         # line 31
cdef class BigQueryParser(SQLParser):                                          # bigquery.pxd:5
    async def filter_conditions(self, sql)        # line 127; Rust call inside try/except Exception (logs warning) (:129-136)
    async def _filter_conditions_cy(self, sql)    # line 139; dict branch :209-220: `if op in COMPARISON_TOKENS:` (:213) else JSON_VALUE(field_expr, '$.<op>') = v (:216-220)

# querysource/types/validators.pyx
cdef class Entity                                                              # line 484
    def quoteString(cls, value, bool_t no_dblquoting=True)                     # line 585 (strip-then-requote; classmethod)
cpdef object is_valid(object key, object value, str T = None, bint noquote = False)  # line 620 (returns value for list/dict; quotes plain strings when noquote=False)

# querysource/exceptions.py
class QueryException(Exception)                                                # line 6 (handlers answer with .code as HTTP status)
class ParserError(QueryException): default_code = 400                          # line 86-87

# querysource/qsurl
def like_escape(value: str) -> str                                             # translate.py:21 (semantics mirrored by M1; NOT imported by parsers)
_MAX_REGEX_PATTERN_LENGTH = 200                                                # residual.py:35 (bound mirrored by M1)
```
```rust
// rust/src/pgsql_parser.rs
fn pg_validate_operator(op: &str) -> bool                                      // line 37 (COMPARISON ∪ VALID ∪ JSONB ∪ PG_TEXT)
fn pg_literal(value: &str) -> String                                           // line 51
const COMPARISON_TOKENS: &[&str]                                               // line 75
const VALID_OPERATORS: &[&str]                                                 // line 78
const JSONB_OPERATORS: &[&str]                                                 // line 82
const PG_TEXT_OPERATORS: &[&str] = &["ILIKE", "NOT ILIKE"];                    // line 90
enum FilterValue { Str, Int, Float, Bool, Dict(Vec<(String, FilterValue)>), List, Null }  // pgsql_parser.rs ~line 100-135 (has Dict; filter_common's does NOT)
fn extract_filter_value(obj) -> FilterValue                                    // line 139 (Dict at :157-167)
fn jsonb_condition(key: &str, dict: &Bound<'_, PyDict>) -> JsonbOutcome        // line 343; PG_TEXT exemption at :361-363
fn process_dict_value(key: &str, entries: &[(String, FilterValue)], _format: Option<&str>) -> Option<String>  // line 474; reads entries[0]; PG_TEXT branch :519-536
fn process_str_value(key, value, name, end, _format) -> Option<String>         // line 603; `~`/`!~` suffix ILIKE at :610-622
pub fn pgsql_filter_conditions(sql, filter_dict, cond_definition) -> PyResult<String>  // line 709
#[cfg(test)] mod tests                                                         // line 792

// rust/src/sql_parser.rs
const COMPARISON_TOKENS: &[&str]                                               // line 14
const VALID_OPERATORS: &[&str] = &["<", ">", ">=", "<=", "<>", "!=", "IS NOT", "IS"];  // line 17
fn validate_operator(op: &str) -> Result<(), String>                           // line 47
fn safe_scalar_value(value: &str) -> String                                    // line 58 (null passthrough; numbers raw; else quote_string(escape_string()))
pub fn filter_conditions(sql, filter_dict, cond_definition) -> PyResult<String>  // line 218; dict branch :246-258 (`validate_operator(&op).is_err()` → continue at :251)
#[cfg(test)] mod tests                                                         // line 623

// rust/src/filter_common.rs
pub enum FilterValue { Str(String), Int(i64), Float(f64), Bool(bool), List(Vec<FilterValue>), Null }  // line 20-27 — NO Dict variant
pub struct FilterEntry { key, value, format_hint }                             // line 43-47
pub fn extract_filter_value(obj) -> FilterValue                                // line 55 (dict falls to str() fallback)
pub fn extract_entries(filter_dict, cond_definition) -> Vec<FilterEntry>       // line 82
pub fn process_entry(entry: &FilterEntry) -> Option<String>                    // line 110; match arms :124-130, `FilterValue::Null => None,` (:129)
pub fn process_str_value(key, value, name, end) -> Option<String>              // line 173
pub fn apply_where_clause(sql: &str, where_cond: &[String]) -> PyResult<String>  // line 206

// rust/src/mssql_parser.rs
pub fn mssql_filter_conditions(sql, filter_dict, cond_definition) -> PyResult<String>  // line 23 (extract_entries → par_iter().filter_map(process_entry) → apply_where_clause)

// rust/src/bigquery_parser.rs
fn process_dict_value(field_expr: &str, entries: &[(String, FilterValue)]) -> Option<String>  // line 181; COMPARISON at :193; JSON_VALUE fallback :198-203
pub fn bq_filter_conditions(...)                                               // line ~303

// rust/src/validators.rs
pub fn field_components(field: &str) -> Vec<(String, String, String)>          // line 120
pub fn escape_string(value: &str) -> String                                    // line 138
pub fn quote_string(value: &str, no_dblquoting: bool) -> String                // line 164

// rust/src/lib.rs
mod filter_common; ... mod validators;                                         // lines 8-24 (add `mod partial_match;`)
#[pymodule] fn _qs_parsers(m: &Bound<'_, PyModule>) -> PyResult<()>            // line 36-37
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `validate_partial_match` (M1) | `AbstractParser._where_element` | call before `is_valid` in the dict branch | `abstract.pyx:572-573` |
| `supports_regex_filter` (M3) | `AbstractParser.set_attributes` / `pgSQLParser.__init__` | default False / set True | `abstract.pyx:69`, `pgsql.pyx:301` |
| `partial_match_condition` (M4) | `pgSQLParser._filter_conditions_cy` dict branch | new `elif op in PARTIAL_MATCH_OPERATORS` after the ILIKE branch | `pgsql.pyx:372` |
| table exemption (M4) | `jsonb_condition` | early `return (False, None)` next to the PG_TEXT one | `pgsql.pyx:270` |
| `lookup()` (M2) | `pg_validate_operator`, `jsonb_condition`, `process_dict_value` | `lookup(op).is_some()` | `pgsql_parser.rs:37`, `:361`, `:491` |
| `sql_partial_match_condition` (M5) | `SQLParser.filter_conditions` Cython dict branch | replaces the discard `continue` | `sql.pyx:161-163` |
| Rust wrapper (M5) | `SQLParser.filter_conditions` | `try: return _rs.filter_conditions(...) except Exception: pass` | `sql.pyx:118-119` (shape from `pgsql.pyx:308-312`) |
| `lookup()` (M2) | `sql_parser::validate_operator` / dict branch | accept table names; render | `sql_parser.rs:47`, `:246-258` |
| `FilterValue::Dict` (M6) | `filter_common::extract_filter_value` / `process_entry` | new variant; `Dict => None` in `process_entry` | `filter_common.rs:55`, `:129` |
| `process_mssql_entry` (M6) | `mssql_filter_conditions` | replaces `process_entry` in the `filter_map` | `mssql_parser.rs:33-36` |
| Cython dict branch (M6) | `msSQLParser._filter_conditions_cy` | inserted before the list branch | `sqlserver.pyx:114` |
| `bq_partial_match_condition` (M7) | `BigQueryParser._filter_conditions_cy` dict branch | `elif op in PARTIAL_MATCH_OPERATORS` before the JSON_VALUE else | `bigquery.pyx:213-216` |
| `lookup()` (M2) | `bigquery_parser::process_dict_value` | table check before JSON_VALUE fallback | `bigquery_parser.rs:193-198` |
| tests (M8) | `pgsql._rs.pgsql_filter_conditions`, `parser._filter_conditions_cy`, `parser.set_where(filter, None)` | direct calls, Redis not needed | `tests/qsurl/test_pg_ilike.py:49-57`, `abstract.pyx:602` |

### Does NOT Exist (Anti-Hallucination)
- ~~`querysource.parsers.partial_matching`~~ and ~~`rust/src/partial_match.rs`~~ — created by M1/M2; nothing else may be imported from them.
- ~~`AbstractParser.supports_regex_filter`~~ — added by M3 (`abstract.pxd`); absent today.
- ~~`FilterValue::Dict` in `rust/src/filter_common.rs`~~ — absent today (only `pgsql_parser.rs` and `bigquery_parser.rs` have a private `Dict` variant); added by M6.
- ~~`querysource.parsers.sql.PG_TEXT_OPERATORS`~~ / ~~`LIKE_OPERATORS`~~ / ~~`TEXT_OPERATORS`~~ — the only text-operator constant is `pgsql.pyx:36 PG_TEXT_OPERATORS`.
- ~~`querysource.types.validators.like_escape`~~ — `like_escape` lives only in `querysource/qsurl/translate.py:21`; parsers do not import qsurl.
- ~~`Entity.pg_literal`~~ — `pg_literal` is a module-level `cdef` in `pgsql.pyx:41`, not an `Entity` method.
- ~~`msSQLParser` dict-value branch~~ — `sqlserver.pyx` has no `isinstance(value, dict)` today.
- ~~`_rs.sql_filter_conditions`~~ — the generic Rust entry point is `_rs.filter_conditions` (`lib.rs:65`).
- ~~`ParserError(code=...)` keyword~~ — construct as `ParserError("message")`; `default_code = 400` applies (`exceptions.py:87`).
- ~~`QueryObject.where_cond`~~ as a parser attribute — the parser attribute is `self.filter` (`abstract.pxd:17`), populated by `_query_filter_sync` (`abstract.pyx:308`).
- ~~`ESCAPE '\'` on generic SQL~~ — rejected: `\` is a literal escape in MySQL; the decided escape char is `!`.
- ~~`wikitoolkit`~~ in `.venv` — not installed (research used grep/read only).

### Edit Sites (Blueprint Anchors)

Verified against: `2f82509b`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/parsers/partial_matching.py` | CREATE | — | — | — |
| `rust/src/partial_match.rs` | CREATE | — | — | — |
| `rust/src/lib.rs` | MODIFY (add `mod partial_match;`) | `mod parseqs;` | `lib.rs:16` | 1 |
| `querysource/parsers/abstract.pxd` | MODIFY (add flag after) | `    cdef public bint string_literal` | `abstract.pxd:48` | 1 |
| `querysource/parsers/abstract.pyx` | MODIFY (import after) | `from ..types.validators import Entity, is_valid, field_components` | `abstract.pyx:19` | 1 |
| `querysource/parsers/abstract.pyx` | MODIFY (default in) | `    cdef void set_attributes(self):` | `abstract.pyx:69` | 1 |
| `querysource/parsers/abstract.pyx` | MODIFY (dict branch) | `            op, v = next(reversed(value.items()))` | `abstract.pyx:572` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY (constants after) | `PG_TEXT_OPERATORS = ('ILIKE', 'NOT ILIKE',)` | `pgsql.pyx:36` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY (helper before) | `cdef tuple jsonb_condition(str col, dict value):` | `pgsql.pyx:234` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY (exemption next to) | `    if op in PG_TEXT_OPERATORS:` | `pgsql.pyx:270` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY (flag in `__init__`) | `    def __init__(self, *args, **kwargs):` — context: directly under `cdef class pgSQLParser(SQLParser):` (`:298`) | `pgsql.pyx:301` | 1 in class (anchor appears once in file) |
| `querysource/parsers/pgsql.pyx` | MODIFY (branch after) | `                    elif op in PG_TEXT_OPERATORS and isinstance(v, str):` | `pgsql.pyx:372` | 1 |
| `rust/src/pgsql_parser.rs` | MODIFY (allowlist) | `fn pg_validate_operator(op: &str) -> bool {` | `pgsql_parser.rs:37` | 1 |
| `rust/src/pgsql_parser.rs` | MODIFY (exemption; ambiguous one-liner) | `        if PG_TEXT_OPERATORS.contains(&op.as_str()) {` — context: inside `fn jsonb_condition`, preceded by the comment `// qsurl text-match operators (FEAT-152) are handled by process_dict_value,` and followed by `return JsonbOutcome::NotJsonb;` | `pgsql_parser.rs:361` | 2 (8-space indent at :361; 4-space at :519) |
| `rust/src/pgsql_parser.rs` | MODIFY (branch) | `fn process_dict_value(` | `pgsql_parser.rs:474` | 1 |
| `querysource/parsers/sql.pyx` | MODIFY (import after) | `from ..types.validators import Entity, field_components` | `sql.pyx:14` | 1 |
| `querysource/parsers/sql.pyx` | MODIFY (wrapper) | `            return _rs.filter_conditions(sql, dict(self.filter), dict(self.cond_definition))` | `sql.pyx:119` | 1 |
| `querysource/parsers/sql.pyx` | MODIFY (branch replaces discard) | `                        # currently, discard any non-supported comparison token` | `sql.pyx:162` | 1 |
| `rust/src/sql_parser.rs` | MODIFY (allowlist) | `fn validate_operator(op: &str) -> Result<(), String> {` | `sql_parser.rs:47` | 1 |
| `rust/src/sql_parser.rs` | MODIFY (dict branch) | `                if validate_operator(&op).is_err() {` | `sql_parser.rs:251` | 1 |
| `querysource/parsers/sqlserver.pyx` | MODIFY (import after) | `from ..types.validators import Entity, field_components` | `sqlserver.pyx:11` | 1 |
| `querysource/parsers/sqlserver.pyx` | MODIFY (dict branch before) | `                if isinstance(value, list):` | `sqlserver.pyx:114` | 1 |
| `rust/src/filter_common.rs` | MODIFY (variant) | `    List(Vec<FilterValue>),` — context: inside `pub enum FilterValue {` (`:20`), before `    Null,` | `filter_common.rs:25` | 1 |
| `rust/src/filter_common.rs` | MODIFY (extract) | `pub fn extract_filter_value(obj: &Bound<'_, pyo3::types::PyAny>) -> FilterValue {` | `filter_common.rs:55` | 1 |
| `rust/src/filter_common.rs` | MODIFY (match arm before) | `        FilterValue::Null => None,` | `filter_common.rs:129` | 1 |
| `rust/src/mssql_parser.rs` | MODIFY (use + entry fn) | `use crate::filter_common::{apply_where_clause, extract_entries, process_entry};` | `mssql_parser.rs:10` | 1 |
| `querysource/parsers/bigquery.pyx` | MODIFY (branch between) | `                    if op in COMPARISON_TOKENS:` | `bigquery.pyx:213` | 1 |
| `rust/src/bigquery_parser.rs` | MODIFY (table check before JSON fallback) | `    // For BigQuery, dict values with non-comparison keys are JSON extraction` | `bigquery_parser.rs:197` | 1 |
| `tests/test_partial_matching_operators.py` | CREATE | — | — | — |
| `tests/test_pgsql_partial_matching.py` | CREATE | — | — | — |
| `tests/test_sql_partial_matching.py` | CREATE | — | — | — |
| `tests/test_mssql_partial_matching.py` | CREATE | — | — | — |
| `tests/test_bigquery_partial_matching.py` | CREATE | — | — | — |
| `tests/test_partial_matching_prevalidation.py` | CREATE | — | — | — |
| `docs/FILTER_OPERATORS.md` | CREATE | — | — | — |
| `querysource/version.py` | MODIFY | `__version__ = '5.2.1'` | `version.py:9` | 1 |

---

## 7. Implementation Notes & Constraints

> Architecture decisions stay with the thinking model. A delegated
> implementation may only express a decision already recorded here and in
> the TASK's implementation blocks — it must never invent an API, choose a
> file, or resolve an open design question.

### Patterns to Follow
- **FEAT-152 ILIKE branch as the template** (`pgsql.pyx:372-399`, `pgsql_parser.rs:519-536`):
  same comment discipline, same `pg_literal` quoting, same dual-path test harness
  (`tests/qsurl/test_pg_ilike.py`). The new branch does **not** strip a pre-quote:
  M3 passes raw strings through, so a leading/trailing `'` in an operand is data.
- **Operator lookup, never name matching, inside builders**: `op in PARTIAL_MATCH_OPERATORS`
  / `lookup(op)` then render from the entry. No builder may list operator names.
- **Validate twice, raise once**: `_where_element` (M3) is the user-facing raise site;
  builders re-validate for the `filter_options` path. Cython raises `ParserError`; Rust
  returns `Err(PyValueError::new_err(msg))` with the identical message text.
- **Rust error → Cython fallback → `ParserError`** on every dialect (M5 adds the wrapper
  to `SQLParser`, mirroring `pgsql.pyx:305-313`).
- **Allowlists stay data, not code**: extend `pg_validate_operator`, `validate_operator`
  via `lookup(op).is_some()`; never string-concatenate a user-supplied operator.
- **Quote once**: pattern built unquoted (`build_like_pattern`), then one dialect quoter.
  `LOWER()` wraps both the column and the already-quoted literal.
- **Cython style** (`.claude/rules/cython-development.md`): module-level `cdef str`
  helpers, typed locals, Google docstrings; `.pxd` updated for the new attribute.
- **Rust style** (`.claude/rules/rust-development.md`): `Bound<'py, T>` API, `PyResult`,
  pure-Rust helpers with `#[cfg(test)]` units; no new `#[pyfunction]`.
- **Build sequence for tests**: `make build-rust && make stage-rust` before `pytest`
  (memory note: Python loads the source-tree `_qs_parsers` `.so`; `build-rust` alone leaves
  it stale). Cython rebuild is required after the `.pxd` change (all parsers cimport
  `AbstractParser`).

### Known Risks / Gotchas
- **`.pxd` struct change rebuilds every parser** (`abstract.pxd` adds a field): any
  `.so` compiled before M3 will fail to import with a layout mismatch. Mitigation: M3's
  task carries the full Cython rebuild and is `parallel: false`.
- **JSONB / JSON_VALUE name shadowing**: a JSONB column filtered by a literal key named
  like a table operator (`{"meta": {"contains": "x"}}`) changes meaning on PostgreSQL
  (was containment) and on BigQuery (was `JSON_VALUE(meta, '$.contains')`). Decision:
  table names win; documented in `docs/FILTER_OPERATORS.md`; AC8 pins the behaviour of
  every other key.
- **`filter_options` bypasses `_where_element`** (`abstract.pyx:450-458`): operands reach
  the builders raw and unvalidated — hence the second validation in every builder (AC6).
- **Rust reads the first dict entry, Cython the last**: unchanged for non-table dicts.
  Multi-key dicts that contain a table name are rejected (`ParserError` "one operator per
  field") by M3 and by every Cython builder (AC14) — path-dependent behaviour is impossible.
- **MySQL backslash literals**: `ESCAPE '\'` is a syntax error on MySQL; generic SQL and
  SQL Server use `!`. SQL Server also treats `[` as a wildcard class opener inside
  `LIKE`; `like_escape_bang` escapes `[` as `![` as well (decision recorded here — add to
  M1/M2 helper and tests).
- **BigQuery backslashes**: `bq_quote_string` may or may not double `\`; M7 verifies
  (§8 Q1) and, if needed, doubles backslashes in the escaped pattern before quoting.
- **Regex cost**: the full qsurl residual policy is mirrored (200-character cap **and**
  the nested-quantifier rejection, `residual.py:35-52`) in M1 and M2; PostgreSQL's engine
  is less backtracking-prone than Python's, but one policy for both entry points is
  simpler to explain and test (design-research S8).
- **qsurl vocabulary divergence** (design-research S1, §8 Q3): the qsurl URL expression
  `startswith` means case-insensitive (translated to `ILIKE`), the new `where_cond`
  operator `startswith` means case-sensitive (U1). No code path mixes them; whether qsurl
  should later be re-mapped onto the new table (e.g. `istartswith`) is a separate decision.
- **Existing dict-operator tests depend on `is_valid` pre-quoting**: M3 only bypasses
  `is_valid` for table operators; `{">=": ...}` and FEAT-152 `ILIKE` keep the pre-quote
  (regression test `test_where_element_keeps_is_valid_for_other_dicts`).
- **Stale extension in CI**: dual-path tests skip the Rust path when the installed `.so`
  predates M2 — a green run is only meaningful after `make build-rust && make stage-rust`
  (AC11 makes the rebuild explicit).

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| — | — | No new Python or Rust dependencies (`pyo3`, `rayon`, `regex` already in `rust/Cargo.toml`; `sqlglot` already a test dependency) |

---

## 8. Open Questions

> Questions that must be resolved before or during implementation.

- [x] **U1 — case sensitivity of startswith / endswith / contains** — *Resolved in proposal*: Both variants. Plain names are case-sensitive (`LIKE`); `i`-prefixed names (`istartswith`, `iendswith`, `icontains`) are case-insensitive (`ILIKE` on PostgreSQL).
- [x] **U2 — dialect scope** — *Resolved in proposal*: All SQL dialects — PostgreSQL, generic `SQLParser` (MySQL/SQLite), SQL Server and BigQuery, in both Cython and Rust builders. Regex stays PostgreSQL-only.
- [x] **U3 — regex semantics** — *Resolved in proposal*: `regex`→`~`, `iregex`→`~*`, `not_regex`→`!~` (`not_iregex`→`!~*`), with a maximum pattern length guard mirroring `_MAX_REGEX_PATTERN_LENGTH` in `querysource/qsurl/residual.py`.
- [x] **U4 — where the ≥3-character rule applies and which exception** — *Resolved in proposal*: `contains` only (and its `i`/`not_` forms), raised as `ParserError` pre-dispatch in `AbstractParser._where_element` so it surfaces on both Rust and Cython paths. `startswith`, `endswith`, `like` accept any length.
- [x] **U5 — negated family** — *Resolved in proposal*: Full `not_*` family for every operator.
- [x] **U6 — `i*` variants on dialects without ILIKE** — *Resolved in proposal*: `LOWER(col) LIKE LOWER(pattern)`, explicit and collation-independent, on generic SQL, SQL Server and BigQuery.
- [ ] **Q1 — Does `bq_quote_string` (`bigquery.pyx:31`, and its Rust twin in `bigquery_parser.rs`) preserve a backslash so that `like_escape` output reaches BigQuery as `\%`?** — *Owner: M7 implementer* (decide by reading the function; if it does not double `\`, double it in `bq_partial_match_condition` before quoting). Does not block M1–M6.
- [ ] **Q3 — qsurl vocabulary alignment (design-research S1)**: qsurl's `startswith` / `contains` / `endswith` are case-insensitive (`ILIKE`) while the new `where_cond` operators of the same name are case-sensitive (U1). Keep the divergence documented (default; qsurl is a non-goal here), or open a follow-up feature to re-map qsurl onto `istartswith` / `icontains` / `iendswith`? — *Owner: Jesus Lara*. Does not block implementation.
- [ ] **Q2 — Should `like_escape_bang` also escape `[` for SQL Server (`![`)?** — *Owner: Jesus Lara*. §7 records the default decision **yes** (T-SQL treats `[...]` as a character class in `LIKE`); confirm or revert before M6 lands.

---

## 9. Design Research Cross-Check

> Independent design opinion from the `codex` seat over the **accepted exploration
> doc** (never over this spec). Model: `gpt-5.6-luna` (codex-cli 0.159.2, reasoning high,
> 5m39s) · Status: completed · Transcript: `sdd/state/FEAT-180/design_research/`
> Every row is a suggestion the reviewer made; the disposition is the spec author's call.
> All 9 suggestions cited only repository paths that exist.

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|
| S1 | Resolve case sensitivity before adding aliases — qsurl maps bare `startswith`/`contains`/`endswith` to ILIKE while the table makes them case-sensitive (api) | ESCALATE | U1 is a requester decision (both variants, plain = sensitive) and the proposal wins over the reviewer; the entry points never share a code path, but the cross-entry-point meaning of the word differs — the human decides whether qsurl is re-mapped later | §8 Q3, §2 vocabulary note, §7 risk |
| S2 | Define an explicit JSON-dict collision policy for PG containment and BigQuery `JSON_VALUE` (api) | CONFIRM | real behaviour change for keys literally named like an operator; policy fixed: table names win on every dialect, alternatives documented | §2 reserved-name note, §7 risk, §5 AC8 |
| S3 | Enforce single-key partial-match dictionaries (first-vs-last entry divergence) (api) | CONFIRM | Rust reads the first entry, `_where_element` the last; multi-key dicts with a table name are now rejected in M3 and in every Cython builder | §2 stage 1, §3 M1 `validate_partial_match_dict`, §5 AC14, §4 tests |
| S4 | Do not rely solely on `_where_element`; validation must be a reusable non-rendering validator invoked before dispatch and from the lifecycle (risk) | CONFIRM | exactly the M1 validator + M3 pre-dispatch + builder re-validation design; Rust `Err` → Cython fallback → `ParserError` is the uniform outcome (M5 adds the missing `SQLParser` wrapper) | §2 stage 3, §3 M1/M3/M5, §5 AC6/AC10 |
| S5 | Make escaping ownership explicit per operator — ready pattern vs raw value (risk) | CONFIRM | pinned by the table's `escape` flag; qsurl stays on the uppercase `ILIKE` branch so it cannot be double-escaped; escaping corpus on both paths | §2 escaping-ownership note, §4 escaping corpus, §5 AC7 |
| S6 | Replace generic-SQL escape assumptions with dialect capabilities or stage per dialect (architecture) | CONFIRM | the feature is staged per dialect (M4–M7) with one rendering helper per builder; the generic parser cannot know its dialect, so it uses the portable `!` escape char instead of a dialect-dependent default | §2 rendered forms notes, §3 M5–M7 |
| S7 | Avoid leaking SQL Server dict support into shared SOQL/CQL parsers via `filter_common` (architecture) | CONFIRM | `process_entry` returns `None` for the new `Dict` variant; only `process_mssql_entry` consumes it; cargo test pins SOQL/CQL output | §3 M6, §4 `test_shared_process_entry_ignores_dict`, §5 AC13 |
| S8 | Reuse the existing regex safety policy including the nested-quantifier rejection (risk) | CONFIRM | U3 asked for a bounded length; adding the residual.py nested-quantifier check keeps one policy for both entry points at no design cost | §3 M1 `NESTED_QUANTIFIER_RE`, §5 AC5, §7 risk |
| S9 | Build a full dialect × path × operator conformance matrix incl. errors, JSON collisions, multi-key input and extension staging (testing) | CONFIRM | added as a single parametrized matrix test on top of the per-dialect files; AC11 pins `make build-rust && make stage-rust` | §4 `test_conformance_matrix`, §5 AC11 |

Summary: **8** confirmed · **0** rejected · **1** escalated.

---

## Worktree Strategy

- **Isolation**: ONE feature worktree for FEAT-180
  (`.claude/worktrees/feat-FEAT-180-filter-with-partial-matching`); the `sdd-coder`
  engine gives each task its own sub-worktree inside it.
- **Module dependency graph** (evidence in §3 "Depends on"):
  - M3 → M1 (imports `validate_partial_match`, `PARTIAL_MATCH_OPERATORS`).
  - M4, M5, M6, M7 → M1 (Cython halves import M1) and → M2 (Rust halves `use crate::partial_match`).
  - M4, M5, M6, M7 → M3 (`supports_regex_filter` flag; M4 sets it True).
  - M8 → M1–M7 (tests render through every builder and `set_where`).
  - M9 independent (docs + version).
  - M1 ∥ M2 (twin contracts, different languages); M4 ∥ M5 ∥ M6 ∥ M7 (distinct files)
    once M1–M3 are merged — expected to run concurrently.
- **Shared files**: `rust/src/lib.rs` (M2 only); `rust/src/filter_common.rs` (M6 only);
  `querysource/parsers/abstract.pyx` / `abstract.pxd` (M3 only). No file is modified by
  two modules.
- **Exclusive resources**: the compiled extensions — the Cython rebuild after M3's `.pxd`
  change and `make build-rust && make stage-rust` after M2/M4–M7 must not run concurrently
  with test runs; the tasks carrying a rebuild are `parallel: false`.
- **Cross-feature dependencies**: none open on these files (FEAT-179 JSONB operators is
  merged on `dev` at `4d57a4af`).

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-07 | Jesus Lara + Claude Fable 5.1 | Initial draft from accepted proposal FEAT-180 (U1–U6 resolved) |
