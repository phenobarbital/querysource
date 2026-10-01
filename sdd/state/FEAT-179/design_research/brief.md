<!--
  sdd/templates/design_research.prompt.md — neutral design-research brief (FEAT-545).
  Rendered by /sdd-spec section 3b and piped to `codex exec ... --output-schema
  design_research.schema.json`.
  FORBIDDEN INPUTS: never paste the spec draft, the spec author's reasoning, a preferred
  conclusion, or any text written by the model that will author the spec. The brief carries
  ONLY the accepted exploration document (brainstorm/proposal) and verified code anchors.
  Placeholders (double-curly-brace tokens, deliberately NOT written with literal braces in
  this comment — a renderer that does a naive whole-document string replace must not also
  rewrite this sentence): problem_statement, constraints_and_goals,
  recommended_option_or_scope, code_context_paths, open_questions, question.
-->
# Independent design review — read-only

You are an independent design reviewer for **QuerySource**, an async-first Python
library for querying heterogeneous data sources (aiohttp, asyncdb, Cython parsers, `querysource/` package).
You have read-only access to the repository in your working directory.

## Rules
1. Read the code you cite. Every `affected_paths` entry must be a repo-relative
   path you actually opened; suggestions with unverifiable paths are discarded.
2. Judge the design intent below against what exists in the repository: what is
   missing, what is risky, what would be simpler, what the codebase already
   provides that the intent re-invents.
3. Do not restate the intent, do not praise it, do not write code. Propose at
   most 12 concrete, falsifiable suggestions, each tagged with a kind
   (`architecture` | `api` | `testing` | `risk` | `alternative`), a risk level and
   your confidence.
4. Output exactly ONE JSON object conforming to the schema you were given — no
   markdown fences, no prose before or after.

## Accepted design intent (verbatim from the exploration document)

### Problem statement
Original request (verbatim): on jsonb based columns, we added AND and OR support, but we need to add NOT (and OR if not exists) support, using an example like:
"where_cond": { "@!": [ {"course": "Pilates Studio"}, {"course": "Pilates Mat"} ], "@$": [ {"course": "Pilates Studio"}, {"course": "Pilates Mat"} ] }

Synthesis: The request extends the JSONB dict-filter operator family of the PostgreSQL parser — whose AND support is implicit multi-key containment via @> and whose OR support is the @>| any-of operator added in commit 8936386 — with two negated forms: @! (none-of: exclude rows containing any listed operand) and @$ (OR-of-NOT: rows lacking at least one listed operand). The dispatch to extend is jsonb_condition, which exists twice and must be changed in lockstep: in Cython at querysource/parsers/pgsql.pyx and in the silently-preferred Rust fast-path at rust/src/pgsql_parser.rs. The new renderers mirror jsonb_any_of_condition, the existing OR helper. The operators are per-column keys ({"col": {"@!": [...]}}) — the ticket example omitted the column name — and one dict may carry both @! and @$, which requires extending the first-key-only dispatch to iterate all operator keys.

### Constraints and goals
- Per-column operator shape: JSONB operators are keys INSIDE a column's dict value ({"col": {"@op": ...}}); the caller loop validates every where_cond key as a safe SQL identifier (FEAT-103) and silently skips non-identifier keys such as a literal "@!". The per-column shape was confirmed by the user.
- Rust path wins silently: filter_conditions() (pgsql.pyx:209-217) tries the Rust extension first and falls back to Cython only on exception; a Rust builder that mis-renders without raising is never corrected. Both builders MUST learn @!/@$ in the same change, and the Rust crate must be rebuilt (maturin develop) before tests.
- First-key-only dispatch: jsonb_condition renders only the first operator key (next(iter(value.items()))) and drops dicts mixing operator and plain keys (operators != len(value)). Supporting @! + @$ in one dict (confirmed requirement) requires iterating all operator keys and AND-ing the rendered groups — a behavioral extension of the dispatch in both builders.
- Literal safety: values render through pg_literal / jsonb_operand (orjson round-trip, E'...' escaping of braces/backslashes) so later format_map passes of build_query survive; all conditions join with AND. New renderers must reuse those helpers exactly as jsonb_any_of_condition does.
- Dual-path tests are the norm: every JSONB filter case runs through both the Rust and Cython paths via the PATHS parametrization in tests/test_pgsql_jsonb_filters.py. @!/@$ ship with cases for both paths, like commit 8936386.
Confirmed semantics: {"col": {"@!": [a, b]}} renders NOT (col @> 'a'::jsonb OR col @> 'b'::jsonb). {"col": {"@$": [a, b]}} renders ((NOT col @> 'a'::jsonb) OR (NOT col @> 'b'::jsonb)). A dict carrying both operators AND-s the two groups.

### Recommended option / probable scope
What's New:
- @! operator token — none-of containment: {"col": {"@!": [a, b]}} -> NOT (col @> 'a'::jsonb OR col @> 'b'::jsonb)
- @$ operator token — OR-of-NOT: {"col": {"@$": [a, b]}} -> ((NOT col @> 'a'::jsonb) OR (NOT col @> 'b'::jsonb))
- One negation helper per builder (Cython + Rust), modeled on jsonb_any_of_condition
- Multi-operator dispatch: a dict carrying several operator keys (e.g. @! + @$) renders each group and ANDs them

What Changes:
- querysource/parsers/pgsql.pyx :: JSONB_OPERATORS, jsonb_condition, new helper(s)
- rust/src/pgsql_parser.rs :: JSONB_OPERATORS, jsonb_condition, new helper(s)
- tests/test_pgsql_jsonb_filters.py — new parametrized cases (both paths)
- querysource/version.py — version bump, per precedent

Non-Goals: querysource/models.py (where_cond already untyped dict); querysource/parsers/abstract.pyx (plumbing operator-agnostic); other dialect parsers (JSONB is PostgreSQL-only); top-level grouping keys in where_cond (rejected in Q&A); querysource/handlers/ and providers/.

Patterns to follow: commit 8936386 (token + helper + dispatch branch per builder + dual-path tests + version bump); TASK-769 ILIKE/NOT ILIKE dual Rust+Cython operator discipline.

Integration risks: stale Rust extension (no exception -> no Cython fallback) — rebuild via maturin develop in the task AC; multi-operator dispatch change alters a code path shared by @>/<@/@>|/->/->> — keep existing single-operator cases byte-identical in tests.

### Verified code anchors (paths only — open them yourself)
querysource/parsers/pgsql.pyx
rust/src/pgsql_parser.rs
tests/test_pgsql_jsonb_filters.py
querysource/models.py
querysource/parsers/abstract.pyx
querysource/version.py

### Questions still open in the exploration document
none

## Question
Given this accepted design intent and these verified code anchors, how would you build it? What is missing, risky, or better done another way?
