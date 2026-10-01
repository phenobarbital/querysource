---
# SDD flow type and base branch (FEAT-145).
type: feature
base_branch: dev
projects: [parsers, rust-parsers]
tags: [jsonb, postgresql, where-cond, filter-operators]
# Intentional ID reuse (see Guardrails escape hatch): FEAT-179 was allocated by
# /sdd-proposal (sdd/proposals/jsonb-not-or-operators.proposal.md, committed
# babac8c) and is already carried by sdd/state/FEAT-179/. Reserving a fresh
# ledger id (next_feature_id: 160) would fork the feature's identity.
reuse_feature_id: FEAT-179
---

# Feature Specification: JSONB NOT (`@!`) and NOT-ALL (`@$`) filter operators

**Feature ID**: FEAT-179
**Date**: 2026-10-01
**Author**: Jesus Lara (jlara@trocglobal.com)
**Status**: approved
**Target version**: 5.1.5

---

## 1. Motivation & Business Requirements

> Proposal: `sdd/proposals/jsonb-not-or-operators.proposal.md` (accepted).
> Research audit: `sdd/state/FEAT-179/`.

### Problem Statement

On JSONB-based columns, `where_cond` already supports AND (implicit multi-key
containment via a single `@>` check) and OR (the `@>|` any-of operator, commit
`8936386`), but there is no way to express negation. Consumers need to exclude
rows whose JSONB column contains any of a list of documents (NOT), and to
match rows that lack at least one of a list of documents (OR-of-NOT), e.g.:

```json
"where_cond": {
    "graduation_details": {
        "@!": [{"course": "Pilates Studio"}, {"course": "Pilates Mat"}],
        "@$": [{"course": "Pilates Studio"}, {"course": "Pilates Mat"}]
    }
}
```

### Goals

- `{"col": {"@!": [a, b]}}` renders `NOT (col @> 'a'::jsonb OR col @> 'b'::jsonb)`
  — none-of: exclude rows containing ANY listed operand.
- `{"col": {"@$": [a, b]}}` renders `((NOT col @> 'a'::jsonb) OR (NOT col @> 'b'::jsonb))`
  — not-all: match rows lacking at least one listed operand.
- One dict may carry several operator keys (e.g. `@!` + `@$`); each group is
  rendered and the groups are AND-ed.
- Both builders (Cython `pgsql.pyx` and Rust `pgsql_parser.rs`) implement
  identical semantics in the same change.
- Operand validation, literal escaping and fail-closed dropping behave exactly
  like `@>|` (`jsonb_any_of_condition`).

### Non-Goals (explicitly out of scope)

- Top-level operator keys in `where_cond` (the ticket example's literal shape)
  — rejected in proposal Q&A in favor of the per-column shape; a literal
  `"@!"` where_cond key remains silently skipped by the FEAT-103 identifier
  validation.
- Changes to `querysource/models.py` (`where_cond` is already an untyped
  `Optional[dict]`), `querysource/parsers/abstract.pyx` (plumbing is
  operator-agnostic), handlers, or providers.
- JSONB operators for other dialect parsers (`sql.pyx`, `bigquery.pyx`,
  `sqlserver.pyx`, …) — JSONB filtering is PostgreSQL-only.
- New negated path-comparison forms (`->`/`->>` stay as they are).

---

## 2. Architectural Design

### Overview

Mirror of commit `8936386` (the `@>|` any-of operator): each new operator is
(1) a token in the two `JSONB_OPERATORS` constants, (2) a small rendering
helper modeled on `jsonb_any_of_condition`, and (3) a dispatch branch in the
two `jsonb_condition` implementations. One behavioral extension on top of the
mirror: `jsonb_condition` currently renders only the FIRST operator key of a
dict (`next(iter(value.items()))` / `dict.iter().next()`); it is extended to
iterate ALL operator keys, render each group, and AND the groups. Rendering
stays fail-closed: if ANY group renders `None` (bad operand), the whole
column's condition is dropped (`(True, None)` / `JsonbOutcome::Skip`),
consistent with today's "one invalid item drops all" behavior.

Two dispatch rules made explicit by the design-research cross-check (§9):

- **Mixed operator classes are rejected deterministically** (S3): a dict
  mixing comparison tokens (`>=`, `<`, …) with JSONB tokens is dropped — the
  current code only inspects the FIRST key, which lets the two builders
  disagree on such dicts; the rewrite must rule them out explicitly on both
  paths (comparison-token-only dicts keep their existing `(False, None)` /
  `NotJsonb` hand-off to the generic path).
- **Empty/non-list operands drop the condition — documented contract** (S4):
  `{"@!": []}` and `{"@$": []}` render nothing (condition omitted), exactly
  like `@>|` today. For `@$` this means "no filter" rather than the
  strictly-logical `FALSE` of an empty OR; this is the repo's fail-open-by-
  omission convention for malformed filters and is pinned by tests and
  helper docstrings.

Rendered forms (confirmed in proposal Q&A):

| Filter value | SQL |
|---|---|
| `{"@!": [a]}` | `NOT (col @> 'a'::jsonb)` |
| `{"@!": [a, b]}` | `NOT (col @> 'a'::jsonb OR col @> 'b'::jsonb)` |
| `{"@$": [a]}` | `NOT (col @> 'a'::jsonb)` (degenerate, equal to `@!`) |
| `{"@$": [a, b]}` | `((NOT col @> 'a'::jsonb) OR (NOT col @> 'b'::jsonb))` |
| `{"@!": [...], "@$": [...]}` | `(<@!-group> AND <@$-group>)` |

### Component Diagram

```
QueryObject.where_cond ──→ AbstractParser (filter plumbing, untouched)
                              │
                              ▼
            pgSQLParser.filter_conditions (pgsql.pyx:209-217)
                 │ Rust importable?            │ fallback on exception
                 ▼                             ▼
  _rs.pgsql_filter_conditions          _filter_conditions_cy
  (rust/src/pgsql_parser.rs)           (querysource/parsers/pgsql.pyx)
                 │                             │
                 ▼                             ▼
      jsonb_condition (Rust)          jsonb_condition (Cython)
        ├─ jsonb_any_of_condition       ├─ jsonb_any_of_condition   (@>|, exists)
        ├─ jsonb_none_of_condition NEW  ├─ jsonb_none_of_condition  NEW (@!)
        └─ jsonb_not_all_condition NEW  └─ jsonb_not_all_condition  NEW (@$)
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `JSONB_OPERATORS` (both builders) | extends | add `'@!'`, `'@$'` tokens |
| `jsonb_condition` (both builders) | modifies | multi-operator-key iteration + 2 dispatch branches |
| `jsonb_any_of_condition` (both builders) | pattern source | new helpers copy its operand validation and literal pipeline |
| `pg_literal` / `jsonb_operand` (both builders) | uses | unchanged; mandatory for escaping |
| `tests/test_pgsql_jsonb_filters.py` | extends | new parametrized cases, both paths |

### Data Models

None — `where_cond` stays `Optional[dict]` (`querysource/models.py:38`).

### New Public Interfaces

No Python-visible API change. The new operators are data-level tokens inside
`where_cond`; the module-level helpers are `cdef`/private-Rust and not exported.

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: Cython operators | yes | helpers + dispatch fixed below; mirror of `jsonb_any_of_condition`; fail-closed `None` | — |
| M2: Rust operators | yes | exact twin of M1, `PyResult<Option<String>>` helpers; `JsonbOutcome::Skip` on failure | — |
| M3: Dual-path tests | yes | case list fixed in §4; anchors fixed in §6 | — |
| M4: Version bump | yes | `querysource/version.py:9` → `5.1.5` | — |

### Module 1: Cython negated-containment operators
- **Path**: `querysource/parsers/pgsql.pyx`
- **Responsibility**: `@!`/`@$` tokens, their renderers, multi-operator dispatch (Cython fallback path)
- **Depends on**: existing `pg_literal`, `jsonb_operand` (same file)
- **Interface Skeleton** *(signatures + docstrings only)*:
  ```python
  # modifies querysource/parsers/pgsql.pyx:30  # verified: querysource/parsers/pgsql.pyx:30
  JSONB_OPERATORS = ('@>', '<@', '@>|', '@!', '@$', '->', '->>',)

  # new helpers, inserted after jsonb_any_of_condition  # verified: querysource/parsers/pgsql.pyx:113
  cdef str jsonb_none_of_condition(str col, object operand):
      """Render ``{"@!": [a, b, ...]}`` as ``NOT (col @> 'a'::jsonb OR ...)``.

      None-of containment: the negated dual of ``jsonb_any_of_condition``.
      Returns None when the operand is not a non-empty list/tuple or any
      item fails ``jsonb_operand`` (fail-closed, drops the condition).
      """

  cdef str jsonb_not_all_condition(str col, object operand):
      """Render ``{"@$": [a, b, ...]}`` as ``((NOT col @> 'a'::jsonb) OR ...)``.

      Not-all containment (OR-of-NOT). Single operand renders
      ``NOT (col @> 'a'::jsonb)``. Same operand validation as above.
      """

  # modified: same signature, iterates ALL operator keys  # verified: querysource/parsers/pgsql.pyx:151
  cdef tuple jsonb_condition(str col, dict value):
      """... (docstring extended: multi-operator dicts AND their groups;
      any group rendering None drops the whole condition)."""
  ```

### Module 2: Rust twin
- **Path**: `rust/src/pgsql_parser.rs`
- **Responsibility**: identical tokens, helpers, and multi-operator dispatch on the preferred fast path
- **Depends on**: existing `pg_literal`, `jsonb_operand`, `JsonbOutcome` (same file); semantics contract shared with M1 (no code dependency)
- **Interface Skeleton**:
  ```rust
  // modifies rust/src/pgsql_parser.rs:82  // verified: rust/src/pgsql_parser.rs:82
  const JSONB_OPERATORS: &[&str] = &["@>", "<@", "@>|", "@!", "@$", "->", "->>"];

  // new helpers after jsonb_any_of_condition  // verified: rust/src/pgsql_parser.rs:235
  /// Render `{"@!": [a, b, ...]}` as `NOT (col @> 'a'::jsonb OR ...)`.
  fn jsonb_none_of_condition(col: &str, operand: &Bound<'_, PyAny>) -> PyResult<Option<String>>;

  /// Render `{"@$": [a, b, ...]}` as `((NOT col @> 'a'::jsonb) OR ...)`.
  fn jsonb_not_all_condition(col: &str, operand: &Bound<'_, PyAny>) -> PyResult<Option<String>>;

  // modified: iterate all operator keys, AND the groups, Skip if any is None
  fn jsonb_condition(key: &str, dict: &Bound<'_, PyDict>) -> JsonbOutcome;  // verified: rust/src/pgsql_parser.rs:290
  ```

### Module 3: Dual-path tests
- **Path**: `tests/test_pgsql_jsonb_filters.py`
- **Responsibility**: parametrized cases for `@!`/`@$` through BOTH code paths; regression lock on existing operators
- **Depends on**: Module 1, Module 2 (and a rebuilt extension — see Worktree Strategy)
- **Interface Skeleton**:
  ```python
  # extends the existing parametrize lists and adds one build_query test
  # verified: tests/test_pgsql_jsonb_filters.py:118 (test_jsonb_condition)
  # verified: tests/test_pgsql_jsonb_filters.py:146 (test_invalid_jsonb_filters_are_dropped)
  async def test_build_query_negation_is_grouped(use_rust: bool, monkeypatch) -> None:
      """``@!``/``@$`` groups stay parenthesized next to other AND-ed conditions."""
  ```

### Module 4: Version bump
- **Path**: `querysource/version.py`
- **Responsibility**: `__version__ = '5.1.5'` per release precedent (commit `8936386` pattern)
- **Depends on**: —
- **Interface Skeleton**: single-line change  `# verified: querysource/version.py:9`

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_jsonb_condition` (new cases) | M1+M2 | `@!` single → `NOT (col @> 'a'::jsonb)`; `@!` multi → `NOT (a OR b)`; `@$` single → `NOT (a)`; `@$` multi → `((NOT a) OR (NOT b))`; ticket example (`graduation_details`) |
| `test_jsonb_condition` (combined) | M1+M2 | `{"@!": [...], "@$": [...]}` in one dict → `(<group> AND <group>)` |
| `test_jsonb_values_are_escaped` (extend) | M1+M2 | quotes/braces/backslashes inside `@!`/`@$` operands stay inside `E'...'` literals |
| `test_invalid_jsonb_filters_are_dropped` (new cases) | M1+M2 | `@!`/`@$` with non-list, empty list, invalid JSON item → condition dropped entirely (S4 contract) |
| mixed-class rejection (new) | M1+M2 | a dict mixing comparison tokens with JSONB tokens is dropped identically on both paths (S3) |
| existing cases | M1+M2 | all pre-existing expected strings byte-identical (regression lock on the dispatch rewrite) |

### Integration Tests

| Test | Description |
|---|---|
| `test_build_query_negation_is_grouped` | full `build_query` pipeline keeps `@!`/`@$` groups parenthesized next to other AND-ed conditions and survives format passes; asserts the sqlglot-parsed tree carries the intended NOT/OR/AND nesting, incl. `@!`+`@$`+scalar combined (S6; mirror of `test_build_query_any_of_is_grouped`, tests/test_pgsql_jsonb_filters.py:195) |
| escaping through `build_query` (extend) | negated operands containing quotes, braces AND backslashes survive the placeholder-cleanup format passes on both paths (S8) |

### Test Data / Fixtures

Reuse the module's existing helpers — `_make_parser`, `_render`, `_where`
(tests/test_pgsql_jsonb_filters.py:56-73) and the `PATHS` dual-path
parametrization. No new fixtures.

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] `{"col": {"@!": [a, b]}}` renders `NOT (col @> 'a'::jsonb OR col @> 'b'::jsonb)` on BOTH paths.
- [ ] `{"col": {"@$": [a, b]}}` renders `((NOT col @> 'a'::jsonb) OR (NOT col @> 'b'::jsonb))` on BOTH paths.
- [ ] A dict with both `@!` and `@$` renders both groups AND-ed, parenthesized.
- [ ] Invalid operands (non-list, empty, bad JSON item) drop the condition on BOTH paths (no partial render, no exception) — the empty-list no-op contract is documented in the helper docstrings (S4).
- [ ] A dict mixing comparison tokens with JSONB tokens is dropped identically on BOTH paths (S3).
- [ ] All pre-existing cases in `tests/test_pgsql_jsonb_filters.py` pass unchanged (expected strings untouched).
- [ ] `maturin develop` rebuild is part of the task flow; the rust-path tests run against the rebuilt extension (not skipped).
- [ ] `pytest tests/test_pgsql_jsonb_filters.py -v` green; `ruff check` clean on touched Python files; `cargo test` green in `rust/`.
- [ ] `querysource/version.py` bumped to `5.1.5`.
- [ ] No breaking changes to existing public API (operators are additive data-level tokens).

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor** — verified against `8b19e70` (dev).

### Verified Imports

```python
from querysource.parsers import pgsql                      # verified: tests/test_pgsql_jsonb_filters.py (existing usage)
from querysource.parsers.pgsql import pgSQLParser          # verified: tests/test_pgsql_jsonb_filters.py (existing usage)
from querysource.models import QueryObject                 # verified: tests/test_pgsql_jsonb_filters.py (existing usage)
from querysource.qs_parsers import _qs_parsers as _rs      # verified: querysource/parsers/pgsql.pyx:21 (inside try/ImportError)
```

### Existing Class Signatures

```python
# querysource/parsers/pgsql.pyx
COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)            # line 27
JSONB_OPERATORS = ('@>', '<@', '@>|', '->', '->>',)                # line 30
JSONB_KEY_SUFFIXES = '|!~#@:'                                      # line 33
cdef str pg_literal(str value)                                     # line 42 — E'...'-escaped literal
cdef str jsonb_dumps(object value)                                 # line 63 — orjson text
cdef str jsonb_operand(object value)                               # line 76 — str operands are parsed as JSON (invalid raises)
cdef str jsonb_any_of_condition(str col, object operand)           # line 91 — @>| renderer; None on bad operand
cdef str jsonb_path_condition(str col, str op, object operand)     # line 116 — ->/->> renderer
cdef tuple jsonb_condition(str col, dict value)                    # line 151 — returns (handled, condition|None)
class pgSQLParser(SQLParser):                                      # line 202
    async def filter_conditions(self, sql)                         # line 209 — Rust-first, Cython fallback on exception
    async def _filter_conditions_cy(self, sql)                     # line 219 — joins conditions with ' AND ' (line 436)
```

```rust
// rust/src/pgsql_parser.rs
const JSONB_OPERATORS: &[&str]                                     // line 82
const JSONB_KEY_SUFFIXES: &[char]                                  // line 86
fn jsonb_operand(obj: &Bound<'_, PyAny>) -> PyResult<String>       // line 205
fn jsonb_any_of_condition(col: &str, operand: &Bound<'_, PyAny>) -> PyResult<Option<String>>  // line 219
fn jsonb_path_condition(...) -> PyResult<Option<String>>           // line 242
fn jsonb_condition(key: &str, dict: &Bound<'_, PyDict>) -> JsonbOutcome  // line 290; col via pg_safe_identifier_key (line 320)
pub fn pgsql_filter_conditions(sql, filter_dict, cond_definition) -> PyResult<String>  // line 625
```

```python
# querysource/models.py
where_cond: Optional[dict]                                         # line 38 (QueryObject)
# querysource/parsers/abstract.pyx
self.filter = self.conditions.pop('where_cond', {})                # line 311
```

### Integration Points

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `jsonb_none_of_condition` (cy) | `jsonb_condition` dispatch | new `op == '@!'` branch | `querysource/parsers/pgsql.pyx:193-197` |
| `jsonb_not_all_condition` (cy) | `jsonb_condition` dispatch | new `op == '@$'` branch | `querysource/parsers/pgsql.pyx:193-197` |
| `jsonb_none_of_condition` (rs) | `jsonb_condition` match | new `"@!"` arm | `rust/src/pgsql_parser.rs:329-334` |
| `jsonb_not_all_condition` (rs) | `jsonb_condition` match | new `"@$"` arm | `rust/src/pgsql_parser.rs:329-334` |
| new helpers (both) | `pg_literal` + `jsonb_operand` | same pipeline as `@>|` | `pgsql.pyx:107-109`, `pgsql_parser.rs:228` |

### Does NOT Exist (Anti-Hallucination)

- ~~`jsonb_none_of_condition` / `jsonb_not_all_condition`~~ — created by this feature; do not import from anywhere today.
- ~~`'@!'` / `'@$'` handling anywhere in the codebase~~ — no parser, model, or handler knows these tokens yet.
- ~~Top-level operator keys in `where_cond`~~ — not supported before or after this feature (Non-Goal); the caller loop's identifier validation (pgsql.pyx:236-247, pgsql_parser.rs `pg_safe_identifier_key`) silently skips them.
- ~~`NOT @>` / `!@>` SQL or token~~ — PostgreSQL has no negated containment operator; negation wraps the check in `NOT (...)`.
- ~~multi-operator rendering today~~ — current dispatch uses only the first key; dicts mixing operator and plain keys are dropped (`operators != len(value)`, pgsql.pyx:191-192 / pgsql_parser.rs:325-326).
- ~~`querysource.rust_parsers` / `qs_parsers.pgsql` module paths~~ — the extension import is exactly `from querysource.qs_parsers import _qs_parsers`.

### Edit Sites (Blueprint Anchors)

Verified against: `8b19e70`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/parsers/pgsql.pyx` | MODIFY | `JSONB_OPERATORS = ('@>', '<@', '@>|', '->', '->>',)` | `pgsql.pyx:30` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY (insert helpers before) | `cdef str jsonb_path_condition(str col, str op, object operand):` | `pgsql.pyx:116` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY (dispatch) | `        if op == '@>|':` | `pgsql.pyx:195` | 1 |
| `rust/src/pgsql_parser.rs` | MODIFY | `const JSONB_OPERATORS: &[&str] = &["@>", "<@", "@>|", "->", "->>"];` | `pgsql_parser.rs:82` | 1 |
| `rust/src/pgsql_parser.rs` | MODIFY (insert helpers before) | `fn jsonb_path_condition(col: &str, op: &str, operand: &Bound<'_, PyAny>) -> PyResult<Option<String>> {` | `pgsql_parser.rs:242` | 1 |
| `rust/src/pgsql_parser.rs` | MODIFY (dispatch) | `            "@>|" => jsonb_any_of_condition(&col, &operand),` | `pgsql_parser.rs:332` | 1 |
| `tests/test_pgsql_jsonb_filters.py` | MODIFY (happy-path cases after) | `    ({"tags": {"@>|": [["x"]]}}, """tags @> '["x"]'::jsonb"""),` | `test_pgsql_jsonb_filters.py:112` | 1 |
| `tests/test_pgsql_jsonb_filters.py` | MODIFY (invalid cases after) | `    {"attrs": {"@>|": [{"a": 1}, "not json"]}},  # one invalid item drops all` | `test_pgsql_jsonb_filters.py:144` | 1 |
| `tests/test_pgsql_jsonb_filters.py` | MODIFY (new test after) | `async def test_build_query_any_of_is_grouped(use_rust: bool, monkeypatch) -> None:` | `test_pgsql_jsonb_filters.py:195` | 1 |
| `querysource/version.py` | MODIFY | `__version__ = '5.1.4'` | `version.py:9` | 1 |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- **Commit `8936386`** (`@>|` any-of): token + helper + dispatch branch per
  builder + dual-path tests + version bump — this feature is its negated twin.
- **TASK-769 / `ec68025`** (ILIKE / NOT ILIKE): dual Rust+Cython operator
  discipline.
- New helpers reuse `jsonb_operand` → `pg_literal` exactly as
  `jsonb_any_of_condition` does (pgsql.pyx:107-109, pgsql_parser.rs:228) —
  never hand-build literals.
- Cython: `cdef` helpers with Google-style docstrings; Rust: `PyResult<Option<String>>`
  helpers, `JsonbOutcome::Skip` on failure (rust-development rules).

### Known Risks / Gotchas

- **Stale Rust extension**: `filter_conditions()` silently prefers Rust
  (pgsql.pyx:209-217, `except Exception: pass`); an un-rebuilt extension
  renders OLD behavior with no error. Mitigation: `maturin develop` is an
  explicit task step and the rust-path tests must NOT be skipped
  (`pgsql.HAS_RUST` must be true in CI/dev where the extension is built).
- **Dispatch rewrite blast radius**: iterating all operator keys changes a
  path shared by `@>`, `<@`, `@>|`, `->`, `->>`. Mitigation: existing expected
  strings in tests are a byte-identical regression lock; single-operator dicts
  must take the exact same rendering path as today.
- **Fail-closed on mixed validity**: if one operator group of a multi-operator
  dict renders `None`, drop the whole column condition (never render half a
  filter — it would silently widen results).
- **`@$` single-operand degeneracy**: `{"@$": [a]}` ≡ `{"@!": [a]}` — document
  in the helper docstring; not an error.
- **Operator chars overlap `JSONB_KEY_SUFFIXES`** (`!`, `@`, `$` is NOT in the
  suffix set): suffix stripping applies to COLUMN names only, never to operator
  keys inside the value dict — do not "normalize" operator keys.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| — | — | no new dependencies; orjson/pyo3/rayon already in use |

---

## 8. Open Questions

> All design questions were resolved during the proposal phase
> (`sdd/proposals/jsonb-not-or-operators.proposal.md` §5).

- [x] Filter shape — *Resolved in proposal*: per-column
  (`{"course_data": {"@!": [...]}}`); the ticket example omitted the column
  name. Only `jsonb_condition` (both builders) changes.
- [x] `@!` semantics — *Resolved in proposal*: none-of —
  `NOT (col @> 'a'::jsonb OR col @> 'b'::jsonb)`, the exact dual of `@>|`.
- [x] `@$` semantics + multi-operator dicts — *Resolved in proposal*:
  OR-of-NOT — `((NOT col @> 'a'::jsonb) OR (NOT col @> 'b'::jsonb))`; one dict
  may carry both `@!` and `@$`, groups AND-ed (dispatch iterates all operator
  keys).
- [ ] Q1 (from design research S7): should CI gain a permanent gate that
  rebuilds the Rust extension and FAILS (rather than skips) the rust-path
  cases when `qs_parsers` is unavailable, so a release can never pass on the
  Cython fallback alone? — *Owner: Jesus Lara* (repo-wide CI change, beyond
  this feature's scope; this feature's AC only requires the rust cases to
  execute for FEAT-179 validation).

---

## 9. Design Research Cross-Check

> Model: `gpt-5.6-luna` · Status: completed
> · Transcript: `sdd/state/FEAT-179/design_research/`

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|
| S1 | One compositional containment-term primitive in both builders (architecture) | CONFIRM | within each builder the new helpers compose on the same `col @> literal::jsonb` term pipeline (`jsonb_operand`+`pg_literal`) and `@>|` output stays byte-identical; no cross-language abstraction is possible | §7 Patterns |
| S2 | Replace first-key dispatch with explicit operator collection (architecture) | CONFIRM | exactly the planned multi-operator iteration; any malformed group keeps the skip/handled outcome | §2 Overview |
| S3 | Mixed comparison/JSONB dicts → explicit deterministic rejection (risk) | CONFIRM | real divergence today (Cython vs Rust disagree on first-key inspection); made an explicit dispatch rule + both-path test | §2 Overview, §4, §5 AC |
| S4 | Specify empty-list behavior separately for @! and @$ (api) | CONFIRM | contract pinned: empty/non-list operands drop the condition (repo convention), documented in docstrings and tested — @$ empty is a no-op, not logical FALSE | §2 Overview, §5 AC |
| S5 | Parenthesize each NOT term and the full OR group (api) | CONFIRM | already the confirmed rendering (never rely on `NOT col @> x` precedence); rendered-forms table is normative | §2 rendered forms |
| S6 | AST assertions for negation grouping and multi-operator composition (testing) | CONFIRM | `test_build_query_negation_is_grouped` asserts sqlglot tree nesting incl. `@!`+`@$`+scalar | §4 Integration Tests |
| S7 | Fail (not skip) rust-path tests when extension stale/absent in release validation (testing) | ESCALATE | feature AC already requires rust cases to execute for FEAT-179; a permanent CI gate is a repo-wide decision for the user | §8 Q1 |
| S8 | Golden escaping corpus through full build_query for negated operands (testing) | CONFIRM | negated operands with quotes+braces+backslashes added to the build_query escaping cases | §4 Integration Tests |

Summary: **7** confirmed · **0** rejected · **1** escalated.

---

## Worktree Strategy

- **Isolation**: ONE feature worktree for FEAT-179
  (`.claude/worktrees/feat-FEAT-179-jsonb-not-or-operators`); the `sdd-coder`
  engine gives each task its own sub-worktree inside it.
- **Module dependency graph**:
  - M3 → M1 (tests render through `_filter_conditions_cy`, which M1 changes)
  - M3 → M2 (tests render through `_rs.pgsql_filter_conditions`, which M2 changes)
  - M1 ∥ M2 — no code dependency (twin implementations of one semantics
    contract, different languages/files); expected to run concurrently.
  - M4 independent.
- **Shared files**: none — each module owns distinct files.
- **Exclusive resources**: the compiled extension artifact — `maturin develop`
  (after M2) and any Cython rebuild must not run concurrently with test runs;
  the task carrying the rebuild is `parallel: false`.
- **Cross-feature dependencies**: none (no other open spec touches
  `pgsql.pyx` / `pgsql_parser.rs`).

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-01 | Jesus Lara + Claude Fable 5 | Initial draft from accepted proposal FEAT-179 |
