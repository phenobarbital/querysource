---
id: FEAT-179
title: Add NOT (@!) and OR-of-NOT (@$) JSONB dict-filter operators to the PostgreSQL parser
slug: jsonb-not-or-operators
type: feature
mode: enrichment
status: review
source:
  kind: inline
  jira_key: null
  jira_url: null
  fetched_at: 2026-10-01
  summary_oneline: Add NOT (@!) and OR-if-not-exists (@$) operator support to JSONB where_cond filtering (AND/OR already exist)
overall_confidence: medium
base_branch: dev
projects: [parsers, rust-parsers]
tags: [jsonb, postgresql, where-cond, filter-operators]
research_state: sdd/state/FEAT-179/
created: 2026-10-01
updated: 2026-10-01
---

# FEAT-179 — Add NOT (`@!`) and OR-of-NOT (`@$`) JSONB dict-filter operators

> **Mode**: enrichment
> **Confidence**: medium
> **Source**: `inline`
> **Audit**: [`sdd/state/FEAT-179/`](../state/FEAT-179/)

---

## 0. Origin

The original request, preserved verbatim. The full source is at
`sdd/state/FEAT-179/source.md`.

> on jsonb based columns, we added AND and OR support, but we need to add
> NOT (and OR if not exists) support, using an example like:
>
> ```json
> "where_cond": {
>     "@!": [
>         {"course": "Pilates Studio"},
>         {"course": "Pilates Mat"}
>     ],
>     "@$": [
>         {"course": "Pilates Studio"},
>         {"course": "Pilates Mat"}
>     ]
> }
> ```

**Initial signals** (extracted, not interpreted):
- Verbs: "add", "support" → feature-shaped (enrichment)
- Named entities: `where_cond`, JSONB columns, operator tokens `@!` and `@$`
- Components / labels: none (inline source)
- Acceptance criteria provided: no (example only)

---

## 1. Synthesis Summary

The request extends the JSONB dict-filter operator family of the PostgreSQL
parser — whose AND support is implicit multi-key containment via `@>` and
whose OR support is the `@>|` any-of operator added in commit `8936386` —
with two negated forms: `@!` (none-of: exclude rows containing any listed
operand) and `@$` (OR-of-NOT: rows lacking at least one listed operand).
The dispatch to extend is `jsonb_condition`, which exists twice and must be
changed in lockstep: in Cython at `querysource/parsers/pgsql.pyx` and in the
silently-preferred Rust fast-path at `rust/src/pgsql_parser.rs`. The new
renderers mirror `jsonb_any_of_condition`, the existing OR helper. Per the
targeted Q&A, the operators are per-column keys (`{"col": {"@!": [...]}}`) —
the ticket example omitted the column name — and one dict may carry both
`@!` and `@$`, which requires extending the first-key-only dispatch to
iterate all operator keys. Recommendation: proceed to `/sdd-spec`.

---

## 2. Codebase Findings

> All entries are grounded in the research findings at
> `sdd/state/FEAT-179/findings/`. **No fabricated paths or symbols.**

### 2.1 Localization

| # | Path | Symbol | Lines | Role | Evidence |
|---|------|--------|-------|------|----------|
| 1 | `querysource/parsers/pgsql.pyx` | `JSONB_OPERATORS` | 30 | operator-token whitelist for dict-typed filter values (Cython) | F002, F004 |
| 2 | `querysource/parsers/pgsql.pyx` | `jsonb_condition` | 151-199 | dispatch mapping operator key → rendered SQL; gets `@!`/`@$` branches | F002 |
| 3 | `querysource/parsers/pgsql.pyx` | `jsonb_any_of_condition` | 86-113 | existing OR (`@>|`) renderer — template for the negated helpers | F002, F004 |
| 4 | `rust/src/pgsql_parser.rs` | `JSONB_OPERATORS` | 82 | Rust twin of the token whitelist | F003 |
| 5 | `rust/src/pgsql_parser.rs` | `jsonb_condition` | 290-340 | Rust dispatch twin | F003 |
| 6 | `rust/src/pgsql_parser.rs` | `jsonb_any_of_condition` | 219-235 | Rust OR renderer — template | F003 |
| 7 | `tests/test_pgsql_jsonb_filters.py` | `test_jsonb_condition` | — | dual-path (rust + cython) parametrized harness for new cases | F001, F005 |
| 8 | `querysource/models.py` | `QueryObject.where_cond` | 38 | request surface; untyped `Optional[dict]`, no operator whitelist upstream | F005 |

### 2.2 Constraints Discovered

- **Per-column operator shape.** JSONB operators are keys *inside* a column's
  dict value (`{"col": {"@op": ...}}`); the caller loop validates every
  `where_cond` key as a safe SQL identifier (FEAT-103) and silently skips
  non-identifier keys such as a literal `"@!"`.
  *Implication*: the ticket's literal top-level shape does not fit the
  mechanism; the per-column shape was confirmed by the user (§5).
  *Evidence*: F002, F005

- **Rust path wins silently.** `filter_conditions()` (pgsql.pyx:209-217)
  tries the Rust extension first and falls back to Cython only on exception;
  a Rust builder that mis-renders without raising is never corrected.
  *Implication*: both builders MUST learn `@!`/`@$` in the same change, and
  the Rust crate must be rebuilt (`maturin develop`) before tests.
  *Evidence*: F003

- **First-key-only dispatch.** `jsonb_condition` renders only the first
  operator key (`next(iter(value.items()))`) and drops dicts mixing operator
  and plain keys (`operators != len(value)`).
  *Implication*: supporting `@!` + `@$` in one dict (confirmed requirement,
  §5) requires iterating all operator keys and AND-ing the rendered groups —
  a behavioral extension of the dispatch in both builders.
  *Evidence*: F002, F003

- **Literal safety.** Values render through `pg_literal` / `jsonb_operand`
  (orjson round-trip, `E'...'` escaping of braces/backslashes) so later
  `format_map` passes of `build_query` survive; all conditions join with AND.
  *Implication*: new renderers must reuse those helpers exactly as
  `jsonb_any_of_condition` does.
  *Evidence*: F002

- **Dual-path tests are the norm.** Every JSONB filter case runs through both
  the Rust and Cython paths via the `PATHS` parametrization.
  *Implication*: `@!`/`@$` ship with cases in
  `tests/test_pgsql_jsonb_filters.py` for both paths, like commit `8936386`.
  *Evidence*: F004, F005

### 2.3 Recent History (Relevant)

| Commit | When | Author | Message | Touched files |
|--------|------|--------|---------|---------------|
| `8936386` | 2026-09-25 | Jesus Lara | support OR and AND operations in jsonb filters | `querysource/parsers/pgsql.pyx`, `rust/src/pgsql_parser.rs`, `tests/test_pgsql_jsonb_filters.py`, `querysource/version.py` |
| `ec68025` | — | — | feat(qsurl-parser): TASK-769 — PostgreSQL ILIKE / NOT ILIKE dict operator (Rust + Cython) | same dual-builder discipline |
| `4d7cccc` | — | — | fix(pgsql): render JSONB filter conditions (@>, <@, ->, ->>) correctly | JSONB rendering baseline |

---

## 3. Probable Scope  *(mode = enrichment)*

### What's New

- **`@!` operator token** — none-of containment:
  `{"col": {"@!": [a, b]}}` → `NOT (col @> 'a'::jsonb OR col @> 'b'::jsonb)`
- **`@$` operator token** — OR-of-NOT:
  `{"col": {"@$": [a, b]}}` → `((NOT col @> 'a'::jsonb) OR (NOT col @> 'b'::jsonb))`
- **One negation helper per builder** (Cython + Rust), modeled on
  `jsonb_any_of_condition`
- **Multi-operator dispatch**: a dict carrying several operator keys (e.g.
  `@!` + `@$`) renders each group and ANDs them

### What Changes

- **`querysource/parsers/pgsql.pyx`**::`JSONB_OPERATORS`, `jsonb_condition`,
  new helper(s) — *Evidence*: F002
- **`rust/src/pgsql_parser.rs`**::`JSONB_OPERATORS`, `jsonb_condition`,
  new helper(s) — *Evidence*: F003
- **`tests/test_pgsql_jsonb_filters.py`** — new parametrized cases (both
  paths): single-operand, multi-operand, grouping next to other AND-ed
  conditions, escaping, invalid-operand dropping, combined `@!`+`@$` dict —
  *Evidence*: F005
- **`querysource/version.py`** — version bump, per precedent — *Evidence*: F004

### What's Untouched (Non-Goals)

- `querysource/models.py` — `where_cond` is already an untyped dict
- `querysource/parsers/abstract.pyx` — `where_cond` → `filter` plumbing is
  operator-agnostic
- Other dialect parsers (`sql.pyx`, `bigquery.pyx`, `sqlserver.pyx`, …) —
  JSONB operators are PostgreSQL-only
- Top-level grouping keys in `where_cond` (the ticket example's literal
  shape) — explicitly rejected in Q&A in favor of the per-column shape
- `querysource/handlers/`, `querysource/providers/` — no upstream operator
  validation exists

### Patterns to Follow

- Commit `8936386`: token + helper + dispatch branch per builder + dual-path
  tests + version bump — *Evidence*: F004
- TASK-769 (`ec68025`): ILIKE/NOT ILIKE dual Rust+Cython operator discipline —
  *Evidence*: F001, F004

### Integration Risks

- **Stale Rust extension**: an env with `qs_parsers` built but not rebuilt
  renders the old behavior silently (no exception → no Cython fallback).
  *Mitigation*: rebuild via `maturin develop` in the task AC; dual-path tests
  catch divergence. *Evidence*: F003
- **Multi-operator dispatch change**: iterating all operator keys alters a
  code path shared by `@>`/`<@`/`@>|`/`->`/`->>`.
  *Mitigation*: keep existing single-operator cases byte-identical in tests
  (regression cases already exist). *Evidence*: F002, F005

---

## 4. Confidence Map

| ID | Claim | Evidence | Confidence | Reasoning |
|----|-------|----------|------------|-----------|
| C1 | The dispatch to extend is `jsonb_condition` at `querysource/parsers/pgsql.pyx:151-199` and `rust/src/pgsql_parser.rs:290-340` | F002, F003 | high | both functions read directly |
| C2 | Existing OR = `@>|` (`jsonb_any_of_condition`); AND = implicit multi-key containment via `@>` | F002, F004 | high | code read + commit `8936386` diff |
| C3 | Both builders must change in lockstep (Rust wins silently when importable) | F003 | high | try/except-pass read at pgsql.pyx:209-217 |
| C4 | Commit `8936386` is the exact structural template | F004 | high | `git show` inspected; same operator family, same files |
| C5 | No layer above the parser validates operator tokens | F005 | high | models.py:38 `Optional[dict]`; abstract.pyx:311 passes through |
| C6 | The ticket's literal top-level `@!` key would be silently skipped today; intended shape is per-column | F002, F005 | medium→resolved | skipping read directly; shape confirmed by user (§5) |
| C7 | `@!` renders none-of: `NOT (col @> a OR col @> b)` | F002 | medium→resolved | confirmed by user (§5) |
| C8 | `@$` renders OR-of-NOT: `(NOT col @> a OR NOT col @> b)` | — | low→resolved | no codebase evidence possible; confirmed by user (§5) |

Distribution: **5** high, **2** medium (resolved), **1** low (resolved).

---

## 5. Open Questions

### Resolved (during proposal phase)

- [x] **What is the intended filter shape for the new operators?** —
  *Resolved*: per-column, like the existing operators
  (`{"course_data": {"@!": [...]}}`); the ticket example omitted the column
  name. Only `jsonb_condition` (both builders) changes.
  *Resolves claims*: C6
- [x] **What SQL should `@!` render?** — *Resolved*: none-of —
  `NOT (col @> 'a'::jsonb OR col @> 'b'::jsonb)`, the exact dual of `@>|`.
  *Resolves claims*: C7
- [x] **What should `@$` render, and may one dict carry both operators?** —
  *Resolved*: OR-of-NOT — `((NOT col @> 'a'::jsonb) OR (NOT col @> 'b'::jsonb))`;
  and yes, one dict may carry both `@!` and `@$`, with the rendered groups
  AND-ed (dispatch extended to iterate all operator keys).
  *Resolves claims*: C8

### Unresolved (defer to spec / implementation)

*(none)*

---

## 6. Recommended Next Step

**`/sdd-spec FEAT-179`** — *Rationale*: localization is high-confidence
(C1–C5), all semantic unknowns were resolved in Q&A, and the change is a
well-bounded mirror of commit `8936386` with no architectural fork.

### Alternatives

- **`/sdd-brainstorm FEAT-179`** — only if the multi-operator dispatch
  extension is considered risky enough to explore alternatives (e.g. a
  combinator-key design like `{"@and": {...}}`).
- **`/sdd-task FEAT-179`** — not recommended: the change spans two builders
  plus tests; small but not single-file.
- **Manual review** — not needed; research completed within budget.

---

## 7. Research Audit

| Artifact | Path |
|----------|------|
| State checkpoints | `sdd/state/FEAT-179/state.json` |
| Source (raw) | `sdd/state/FEAT-179/source.md` |
| Research plan | `sdd/state/FEAT-179/research_plan.json` |
| Findings (digests) | `sdd/state/FEAT-179/findings/F001-*.md` … `F005-*.md` |
| Synthesis (JSON) | `sdd/state/FEAT-179/synthesis.json` |

**Budget consumed**:
- Files read: 3 / 40
- Grep calls: 4 / 25
- Git calls: 2 / 10
- Wiki calls: 5 (free)
- Truncated: **no**

**Mode determination**: `auto` → resolved to `enrichment` (additive verbs:
"add", "support").

---

## 8. Provenance

| Field | Value |
|-------|-------|
| Generated by | `/sdd-proposal v1.0` |
| Synthesis prompt | `sdd/templates/synthesis.prompt.md v1.0` |
| Plan prompt | `sdd/templates/research_plan.prompt.md v1.0` |
| Schema versions | state=1.0, synthesis=1.0, research_plan=1.0 |
| Operator | Jesus Lara (jlara@trocglobal.com) + Claude Fable 5 |
