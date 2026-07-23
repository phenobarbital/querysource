---
id: FEAT-104
title: Server-Side Jinja2 Templating for SQL Execution (full Redash parity)
type: feature
mode: investigation
status: discussion
source:
  kind: inline
  jira_key: null
  summary: Frontend FEAT-174 added client-side regex support for {{ name }} / {{ name | default(...) }}; full Redash parity (for/if blocks, arbitrary filters) needs server-side Jinja rendering, which is a new SSTI + SQL-injection trust boundary that must be designed and owned here, not in the frontend.
base_branch: dev
confidence: medium
research_state: manual (ad-hoc cross-repo investigation; no automated /sdd-proposal pipeline run — see §7)
---

# FEAT-104 — Server-Side Jinja2 Templating for SQL Execution (full Redash parity)

## §0 Origin

Inline request, originating from the frontend team (`navigator-frontend-next`,
FEAT-174 — QuerySource Jinja-Style Parameters). Users migrating queries from
Redash paste SQL using full Jinja syntax: `{{ tenant }}`, `{{ firstdate |
default("2024-01-01") }}`, and — critically — constructs the frontend
explicitly **cannot** and **should not** emulate: `{% if %}` / `{% for %}`
blocks and arbitrary filters (`| upper`, `| join(',')`, etc.).

FEAT-174 (frontend, merged) intentionally scoped these out as "detect and
block, don't emulate" (see its spec §8 Q1, and Known Risks: *"Do NOT propose
wiring backend Jinja into SQL... rendering user-supplied SQL through a full
Jinja env is an SSTI/injection surface — FEAT-103's validated substitution
exists precisely to avoid that. Frontend-only stands."*). This proposal is
the formal follow-up: **if** we want real Redash parity, the rendering has
to happen here, server-side, and it needs its own security-reviewed design —
it is not a frontend decision to make unilaterally.

**Scope note**: this is distinct from the `fix/raw-query-cond-definition-
wiring` work (FEAT-103 follow-up, feeding frontend FEAT-175 multi-value
params) — **now merged to `dev`** (`df57f1a`/`db15211`/`d31579c`, confirmed
at proposal-write time). That work stayed inside the existing `{name}`
`format_map` substitution model (wiring `cond_definition` through so the
Rust validator's `array` type hint actually gets used). This proposal is
about a **different, larger** capability: actual Jinja control-flow
(loops, conditionals, arbitrary filters) — which the `{name}` model cannot
express at all, no matter how well `cond_definition` is wired.

## §1 Synthesis Summary

### Problem

QuerySource today has exactly two SQL execution paths, and **neither**
understands Jinja:

1. **Ad-hoc run** (`/api/v1/queries/run`, `Executor.query()`) — executes the
   submitted query string verbatim, with **zero** server-side substitution
   of any kind. All parameter resolution must happen client-side before the
   request is sent. This is why FEAT-174 had to be a pure frontend feature.
2. **Saved slug execution** (`raw_query()` in `providers/default.py` and its
   mysql/cassandra/documentdb/sqlserver siblings) — does `{name}`-style
   `format_map` substitution, two-phase: trusted `self.replacement` first,
   then user `conditions` validated through the Rust `safe_format_map_
   validated` (injection-marker + SQL-keyword denylist, identifier
   allow-list). This is a scalar-value substitution model — it has no
   concept of loops or conditionals.

A Jinja2 template engine (`TemplateParser`, `querysource/template/parser.py`)
**already exists** in the codebase, but it is wired to exactly two
consumers — `handlers/service.py:47` (HTML view rendering) and `outputs/
writers/report.py:232` (report output writer) — both of which render
**trusted, project-authored** templates. Neither is anywhere near the SQL
execution path.

### Why this can't just be "wired in"

Reusing `TemplateParser` for user-submitted SQL text is not a wiring change,
it's a trust-boundary change:

- `TemplateParser` builds a plain `jinja2.Environment`, not a
  `jinja2.sandbox.SandboxedEnvironment`. A plain `Environment` allows
  attribute traversal (`{{ ''.__class__.__mro__[1].__subclasses__() }}`-style
  gadgets) — rendering **arbitrary user text** through it is a **Server-Side
  Template Injection (SSTI) → RCE** surface, categorically worse than SQL
  injection.
- Even with a sandboxed environment, the *output* of a Jinja render is still
  spliced into a SQL string. Loop/conditional constructs mean the render can
  now emit **structurally different SQL** (extra `WHERE` clauses, extra
  `IN (...)` members, whole extra joins) — not just scalar values. The
  existing Rust validator (`safe_dict.rs::check_injection`,
  `SQL_KEYWORDS`/`INJECTION_MARKERS` denylist) was designed for **scalar
  value substitution**; it has not been evaluated against arbitrarily
  Jinja-generated SQL fragments.
- Template rendering has its own DoS surface (large/nested loops) that SQL
  parameter substitution does not.

### Recommendation (preliminary)

Full Jinja support for SQL is technically feasible but should be treated as
its own security-reviewed initiative, gated behind a sandboxed, allow-listed
rendering path that is architecturally separate from the trusted
`app['templating']` instance, with the existing Rust validator re-applied to
every leaf value the render produces (defense in depth, not a replacement).
See §3 for the concrete requirements.

## §2 Codebase Findings

### §2.1 Localization

| # | Path | Symbol | Role | Verified |
|---|------|--------|------|----------|
| 1 | `querysource/queries/executor.py` | `Executor.query()` (L113-220) | Ad-hoc run path — `db.query(self._query.query, **kwargs)`, **no substitution of any kind** | read, this session |
| 2 | `querysource/providers/default.py` | `defaultProvider.raw_query()` (L68-85) | Saved-slug path — `{name}` `format_map`, two-phase (trusted replacement + Rust-validated conditions) | read, this session |
| 3 | `querysource/providers/{mysql,cassandra,documentdb,sqlserver}.py` | `raw_query()` / `get_raw_query()` | Same `{name}` pattern, per-driver | grep, this session |
| 4 | `querysource/template/parser.py` | `TemplateParser` (L36-150) | Existing Jinja2 engine — plain `Environment`, async render, filters (`jsonify`, `datetime`) | read, this session |
| 5 | `querysource/services.py` | `app['templating'] = self` | App-wide registration of the trusted `TemplateParser` instance | grep, this session |
| 6 | `querysource/handlers/service.py:47` | `tpl = app['templating']` | Consumer #1 — HTML view rendering (trusted, project-authored templates) | grep, this session |
| 7 | `querysource/outputs/writers/report.py:232` | `self.tpl = request.app['templating']` | Consumer #2 — report output writer (trusted templates) | grep, this session |
| 8 | `rust/src/safe_dict.rs` | `check_injection()` (L48-63), `safe_format_map_validated_rust()` (L100+) | Injection-marker denylist (`--`, `/*`, `;`, `\x`, `\u`), SQL-keyword denylist, identifier allow-list `[A-Za-z0-9_.]` | read, this session |
| 9 | `querysource/providers/abstract.py` (L28-36, per frontend FEAT-174 audit) | `BaseProvider.replacement` | Reserved trusted names (`fields`, `filterdate`, `firstdate`, `lastdate`, `where_cond`, `and_cond`, `filter`) pre-filled before user conditions | cited from FEAT-174 audit (frontend repo), not re-verified this session |

### §2.2 Constraints Discovered

- **Two disjoint execution paths, two disjoint substitution models.** Any
  Jinja rendering added here has to be plumbed into *both* `Executor.query()`
  (ad-hoc) and every provider's `raw_query()`/`get_raw_query()` (saved
  slugs) to reach parity — it's not a single choke point. *Evidence*: F1, F2,
  F3.
- **`TemplateParser` is a shared, app-wide, trusted-template instance**
  (`app['templating']`). It must **not** be repurposed for user SQL text —
  any new rendering path needs its **own** `SandboxedEnvironment` instance,
  separate from `app['templating']`, so the trust boundary is structurally
  impossible to blur. *Evidence*: F4, F5, F6, F7.
- **The Rust validator is scalar-oriented.** `check_injection()` and the
  SQL-keyword/identifier denylists were written for `{name}` → single-value
  substitution. They have not been exercised against multi-value output from
  a loop (e.g., a `{% for %}` emitting `'a','b','c'`) or against
  structurally-varying SQL (conditional `WHERE` fragments). This needs
  explicit test coverage before it can be trusted as the second line of
  defense. *Evidence*: F8.
- **Adjacent work, now merged to `dev`** (`fix/raw-query-cond-definition-
  wiring` branch, FEAT-103 follow-up) wired `cond_definition` through
  `safe_format_map_validated` so the existing `array` type hint actually
  reaches the Rust validator. This proposal's scope starts **after** that
  work — it does not duplicate it and is not blocked by it (confirmed
  merged as of this session: `df57f1a`/`db15211`/`d31579c` are on `dev`).
  *Evidence*: git log on `dev` (this session).
- **Frontend has already drawn the line.** `navigator-frontend-next`
  FEAT-174 (merged) explicitly detects `{% %}` blocks and non-`default`
  filters and **blocks** Test/Run client-side with a message naming the
  unsupported construct, rather than attempting any emulation. If this
  proposal is accepted, that frontend guard would need to be relaxed or
  turned into a soft warning once the backend can actually render these
  constructs — cross-repo coordination required. *Evidence*: FEAT-174 spec
  (frontend repo, `sdd/specs/querysource-jinja-params.spec.md` §2, §8 Q1).

### §2.3 Recent History (Relevant)

| Commit | Branch | Message |
|--------|--------|---------|
| `df57f1a` | `fix/raw-query-cond-definition-wiring` | test: integration coverage for cond_definition wiring in raw_query (FEAT-103) |
| `db15211` | `fix/raw-query-cond-definition-wiring` | fix(rust): case-insensitive cond_definition type hints, unblock cargo test (FEAT-103) |
| `d31579c` | `fix/raw-query-cond-definition-wiring` | fix(providers): wire cond_definition into safe_format_map_validated (FEAT-103) |
| `8b39314` | `dev` | sdd: mark FEAT-103 malforming-queryslug-issue spec as approved |

No commits in the last 30 days touch `template/parser.py` — the Jinja2
engine's use is stable/dormant with respect to this proposal's concern.

## §3 Probable Scope

> Mode: investigation. This section sketches the shape of an eventual spec —
> it is not an implementation plan. Treat every item below as something a
> future `/sdd-spec FEAT-104` (or a dedicated security design review) must
> resolve explicitly, not something to build directly from this document.

### What's New

- **A dedicated `SandboxedEnvironment`** (or `ImmutableSandboxedEnvironment`)
  for rendering user-submitted SQL templates — structurally separate from
  `app['templating']`. Never shares state, filters, or globals with the
  trusted engine.
- **An allow-list of filters/globals** exposed to this sandbox: `default`,
  plus perhaps date/string formatting helpers already deemed safe — nothing
  that exposes Python internals (no `attr`, no `import`, no arbitrary
  attribute chains).
- **A render-time budget**: timeout (e.g. `asyncio.wait_for`) and/or a loop-
  iteration ceiling, to bound the DoS surface of user-controlled `{% for %}`.
- **Post-render validation**: every scalar value that a Jinja render
  produces (including ones generated inside a loop) must still pass through
  the existing Rust `check_injection`/keyword-denylist path before being
  spliced into the final SQL string. Jinja rendering does not replace this
  layer, it sits in front of it.
- **A pre-render gate**: port (or share) the frontend's `detectUnsupportedJinja`
  classification (block vs. `default`-only) as a first-pass filter here too —
  defense in depth, not reliance on the frontend's guard alone (the ad-hoc
  `/api/v1/queries/run` endpoint has no frontend gate to depend on if called
  directly, e.g. from a script or another integration).
- **A dedicated SSTI test suite** — payload-driven (public SSTI payload
  lists exist for Jinja2), proving the sandbox holds under known escape
  techniques, following the existing test-coverage precedent
  (`tests/unit/test_raw_query_cond_definition_wiring.py`).

### What Changes

- **`querysource/queries/executor.py::Executor.query()`** — currently zero
  substitution; would need a new pre-execution rendering step (behind a
  feature flag) for the ad-hoc path to reach parity with saved slugs.
  *Evidence*: F1.
- **`querysource/providers/*.py::raw_query()`/`get_raw_query()`** (all
  drivers) — would need to call the new sandboxed renderer for the
  structural (loop/conditional) parts, then continue running the existing
  `{name}` scalar-substitution + Rust validation on the result. *Evidence*:
  F2, F3.

### What's Untouched (Non-Goals)

- **`app['templating']` / `TemplateParser`** itself — stays exactly as-is,
  serving only trusted, project-authored templates (HTML views, reports).
  Not extended, not repurposed.
- **Macros, includes, or any multi-template composition** — out of scope
  even if this proposal is accepted; a single self-contained template string
  per query is the ceiling being discussed here, matching Redash's own
  model.
- **Any change to the existing `{name}` format_map path itself** — that
  stays as the final substitution/validation layer even after Jinja
  rendering is introduced.
- **The `cond_definition` wiring work** (`fix/raw-query-cond-definition-
  wiring`, now merged to `dev`) — separate effort, not superseded or
  revisited by this proposal.

### Integration Risks

- **RCE via SSTI if the sandbox is misconfigured or bypassed** — the single
  highest-severity risk in this proposal. Mitigation: dedicated security
  review + payload-based test suite before any merge to `dev`; feature-flag
  the capability per-tenant/per-datasource for gradual rollout.
  *Evidence*: F4-F7.
- **Regressions in the Rust validator's assumptions** once it starts
  validating loop-generated values instead of single scalars — needs
  explicit new test cases (multi-value output, empty-loop output, nested
  quoting). *Evidence*: F8.
- **Cross-repo coordination** — if this ships, `navigator-frontend-next`
  FEAT-174's `detectUnsupportedJinja` block-on-Test/Run guard needs a
  follow-up change (soft-warn instead of hard-block, or drop entirely if the
  backend becomes authoritative). *Evidence*: F174 (frontend spec, cited
  above).

## §4 Confidence Map

| ID | Claim | Evidence | Confidence | Reasoning |
|----|-------|----------|------------|-----------|
| C1 | Ad-hoc run (`Executor.query()`) does zero server-side substitution today | F1 | high | direct read of the method body |
| C2 | Saved-slug `raw_query()` uses `{name}` format_map, not Jinja | F2, F3 | high | direct read + grep across all provider files |
| C3 | `TemplateParser` exists and is Jinja2-based but has only 2 non-SQL consumers | F4, F5, F6, F7 | high | direct read + exhaustive grep for `app['templating']` usage |
| C4 | `TemplateParser` uses a plain (non-sandboxed) `Environment` | F4 | high | direct read of `setup()` — only `Environment(...)`, no `SandboxedEnvironment` import anywhere in the file |
| C5 | The Rust validator's denylist approach has not been tested against Jinja-loop-generated multi-value output | F8 | medium | inferred from test file names/scope, not from running the test suite and inspecting coverage directly |
| C6 | The `fix/raw-query-cond-definition-wiring` branch is a distinct, non-overlapping effort from this proposal's scope | git log (F: commits df57f1a/db15211/d31579c) | medium | based on commit messages and diffed file list, not a full read of that branch's spec |

Distribution: **4** high, **2** medium, **0** low.

## §5 Open Questions

### Resolved (during proposal discussion)

- [x] **Q2: Ad-hoc endpoint scope** — should `/api/v1/queries/run` even
      support full Jinja, given it has no PBAC-scoped "saved, reviewed
      artifact" status the way a slug does? — *Resolved by Juan
      (this session)*: **both paths** — ad-hoc (`/api/v1/queries/run`) AND
      saved-slug execution should support full Jinja, matching Redash's own
      UX where even an unsaved "New Query" supports full templating. This
      means the pre-execution rendering step in §3 ("What Changes") is
      required in **both** `Executor.query()` and every provider's
      `raw_query()`/`get_raw_query()` — not slug-only. It also means Q2's
      "smaller surface, slug-only" mitigation option is off the table, which
      raises the importance of Q1 (security sign-off) and the sandboxing
      requirements in §3, since the ad-hoc path has no save/review gate at
      all in front of it.

### Unresolved (needs dedicated investigation before spec)

- [ ] **Q1: Who owns the security sign-off for the sandboxed environment
      design?** — *Owner*: tbd (likely needs someone outside the immediate
      feature team). *Blocks*: any implementation work.
- [ ] **Q3: Rollout mechanism** — per-tenant feature flag? Per-datasource?
      Global with a kill switch? — *Status*: flagged by Juan as needing its
      own investigation (not resolved here). *Owner*: tbd.
- [ ] **Q4: What exactly goes on the filter/global allow-list?** Needs a
      concrete, reviewed list (likely: `default`, `upper`, `lower`, maybe a
      date-formatting helper) — not "whatever `TemplateParser` already
      has," since that includes extensions (`jinja2_time`, `jinja2.ext.do`,
      humanize) that were never vetted for untrusted input. *Status*:
      flagged by Juan as needing its own investigation (not resolved here).
      *Owner*: tbd.

### Deferred (explicitly non-blocking)

- [ ] **Q5: Frontend follow-up sequencing** — does `navigator-frontend-next`
      need to ship its relaxation of `detectUnsupportedJinja` in lockstep
      with this, or can the backend ship first? — *Deferred by Juan (this
      session)*: **not blocking for now** — "eso lo podemos ver después,
      nosotros no es bloqueante por ahora." The frontend guard staying
      conservative (blocking client-side) causes no harm even after the
      backend gains full Jinja support; the two teams will revisit
      sequencing once this proposal moves toward implementation.

> Q2 resolved (both paths — the harder option, security-wise). Q3 and Q4
> still need dedicated investigation before this can become a spec. Q1
> remains open and is now more load-bearing given Q2's resolution (no
> slug-only fallback to shrink the attack surface). Q5 is parked, not a
> blocker. Net: still not ready for `/sdd-spec` — proceed per §6.

## §6 Recommended Next Step

**Security design review, then `/sdd-brainstorm FEAT-104`** — *Rationale*:
the codebase findings are high-confidence and the shape of the problem is
clear, but the open questions (§5) are trust-boundary decisions, not
implementation details. A `/sdd-spec` written before those are resolved
would either bake in an unreviewed security design or leave the spec full of
unresolved placeholders. `/sdd-brainstorm` is the right next step to explore
the sandboxing architecture options (e.g., `SandboxedEnvironment` vs.
`ImmutableSandboxedEnvironment` vs. a hand-rolled restricted-grammar
subset that only supports `for`/`if`/`default` and nothing else) with
security input before committing to one.

### Alternatives

- **Reject / defer indefinitely** — keep the frontend-only, "detect and
  block" model from FEAT-174 as the permanent ceiling. Legitimate option if
  the security review concludes the RCE risk isn't worth the UX gain for
  the current user base (mostly internal/trusted analysts migrating from
  Redash, who may already have DB-level access anyway — worth weighing
  explicitly).
- **Narrower alternative**: instead of full Jinja, design a **small, closed
  grammar** for just `{% for x in list %}...{% endfor %}` (to cover the
  `IN (...)` multi-value case) and `{% if cond %}...{% endif %}` (to cover
  optional `WHERE` fragments), hand-parsed rather than routed through a
  general-purpose Jinja2 sandbox. Meaningfully smaller attack surface than
  "support arbitrary Jinja2," and may cover the actual Redash-migration
  cases without needing a sandboxed general-purpose template engine at all.
  Worth evaluating in the brainstorm as the leading alternative to "just use
  `SandboxedEnvironment`."

## §7 Research Audit

This proposal was produced from a manual, conversational investigation
(Claude + Juan Ruffato) spanning both repos — `navigator-frontend-next`
(where FEAT-174/175 live) and `querysource` (this repo) — **not** from the
automated `/sdd-proposal` pipeline (no `sdd/state/FEAT-104/` research
session exists). Findings were gathered via direct `grep`/`read` on files
cited in §2.1, plus `git log`/`git diff --stat` on the
`fix/raw-query-cond-definition-wiring` branch. No code was written or
modified in either repo as part of this proposal.

| Metric | Value |
|--------|-------|
| Repos inspected | 2 (`navigator-frontend-next`, `querysource`) |
| Files read (this repo) | 6 (`executor.py`, `default.py`, `parser.py`, `safe_dict.rs`, `validators.rs` excerpt, `malforming-queryslug-issue.spec.md` excerpt) |
| Grep queries (this repo) | ~8 |
| Git queries | 3 (`git log`, `git diff --stat`, `git branch --show-current`) |
| Truncated | no |
| State directory | none (manual session) |
