---
id: FEAT-154
title: Lazy-load output writers (PDFWriter/weasyprint first) so QS and the HTTP service stop paying ~0.45 s at import
slug: lazy-import-writers
type: feature
mode: enrichment
status: accepted
source:
  kind: inline
  jira_key: null
  jira_url: null
  fetched_at: 2026-09-29
  summary_oneline: Lazy-import output writers (esp. PDF/weasyprint) to cut QuerySource startup time in library and HTTP server modes
overall_confidence: high
base_branch: dev
projects: [outputs, handlers]
tags: [lazy-import, startup-time, weasyprint, pdf, writers, performance]
research_state: sdd/state/FEAT-154/
created: 2026-09-29
updated: 2026-09-29
---

# FEAT-154 — Lazy-load output writers

> **Mode**: enrichment
> **Confidence**: high
> **Source**: `inline`
> **Audit**: [`sdd/state/FEAT-154/`](../state/FEAT-154/)

---

## 0. Origin

> lazy-import-writers -- actualmente todos los writers (includo PDF que depende de
> weasyprint) hace un import on startup, lo que ralentiza la carga de Querysource
> en modo normal y servidor http

Follow-up from the requester, added during research:

> on querysource.services > outputs > writers > PDFWriter -> weasyprint is an
> example of code that can be lazy-imported

**Initial signals**:
- Verbs: "hace un import on startup", "ralentiza la carga" → performance improvement, not a bug
- Named entities: writers, PDF, weasyprint, `querysource.services`
- Modes affected: library ("modo normal") and HTTP server
- Acceptance criteria provided: no

---

## 1. Synthesis Summary

Every output writer is imported when the process starts. `querysource/outputs/writers/__init__.py`
loads all 13 writer classes, and `querysource/outputs/output.py` builds a static `WRITERS`
dict from them. The only one that matters is `PDFWriter`: `pdf.py` imports `weasyprint` at
module level, which costs about 440–610 ms (about 27% of `import querysource.queries.qs`). Every
other writer costs under 3 ms because it already defers pandas, bokeh, plotly and seaborn.
Both entry modes pay this cost. Library mode goes through
`querysource/queries/base.py` → `outputs.dt` → `querysource/outputs/__init__.py` →
`output.py` → writers. HTTP mode goes through `querysource/services.py` → handlers → the
same chain. The recommendation has three parts:
- Turn `WRITERS` into a lazy, cached registry.
- Make the two package `__init__`s lazy (PEP 562).
- Import weasyprint inside `PDFWriter.get_response`, and move its synchronous render off the event loop.

---

## 2. Codebase Findings

### 2.1 Localization

| # | Path | Symbol | Lines | Role | Evidence |
|---|------|--------|-------|------|----------|
| 1 | `querysource/outputs/writers/__init__.py` | (module) | 1-17 | Eagerly imports all 13 writer classes | F001 |
| 2 | `querysource/outputs/output.py` | `WRITERS` | 19-62 | Static map from 18 format keys to classes, built at import | F002 |
| 3 | `querysource/outputs/output.py` | `DataOutput.response` | 211-234 | Only consumer of `WRITERS`; json fallback on `KeyError` | F002 |
| 4 | `querysource/outputs/writers/pdf.py` | `HTML` / `PDFWriter.get_response` | 7, 48-54 | Top-level `from weasyprint import HTML`; sync `write_pdf` inside `async def` | F003, F004 |
| 5 | `querysource/outputs/__init__.py` | `DataOutput` | 1-6 | Package init loads `output.py` on *any* `querysource.outputs.*` import | F006 |
| 6 | `querysource/queries/base.py` | `OutputFactory` import | 14 | Library-mode entry into the outputs package | F006, F009 |
| 7 | `querysource/services.py` | `from .handlers import` | 21-22 | HTTP-mode entry: handlers → `queries.qs` → `queries.base` → outputs | F009 |

Measured import cost (`python -X importtime`, cumulative, local `.venv`, weasyprint 70.0), from F005 and F009:

| Import | Total | weasyprint share |
|---|---:|---:|
| `querysource.queries.qs` (3 runs) | 1.62–1.66 s | 0.44–0.45 s (~27%) |
| `querysource.services` | 1.96 s | 0.50 s |
| `querysource.handlers.service` | 1.79 s | 0.45 s |
| `querysource.outputs.dt` | 1.38 s | 0.40 s |
| any writer other than pdf | 0.2–2.5 ms | — |

Importing weasyprint also writes three `weasyprint.progress` INFO lines to the log at startup (F005).

### 2.2 Constraints Discovered

- **`WRITERS` is part of the test contract.** `tests/qsurl/test_error_envelope.py:59` calls
  `monkeypatch.setitem(output_module.WRITERS, "json", _StubWriter)`.
  *Implication*: `WRITERS` must remain a module-level mutable mapping in
  `querysource.outputs.output`. Lookups must accept either a class that has already been set
  or a lazy spec that has not been resolved yet. *Evidence*: F007
- **Direct submodule imports must keep working.** Tests import
  `querysource.outputs.writers.csv` / `.tsv` and `querysource.outputs.output.DataOutput`
  directly. *Implication*: no module may be renamed or moved, and
  `from querysource.outputs.writers import PDFWriter` must still resolve, via a PEP 562
  `__getattr__`. *Evidence*: F007
- **Invalid-format fallback.** An unknown format currently logs a warning and falls back to
  `json` (F002). The lazy registry has to keep that behaviour. Resolving a known key must never
  degrade to json silently: a genuine `ImportError` has to surface. *Evidence*: F002
- **Other writers already follow the pattern.** pandas, bokeh, plotly, seaborn, matplotlib
  and pygal are already imported inside methods (F003). `pdf.py` is the only exception among the
  active writers. *Evidence*: F003
- **Async-first convention.** `HTML(string=...).write_pdf()` is CPU-bound, blocking work
  inside `async def get_response`. *Evidence*: F004

### 2.3 Recent History (Relevant)

| Commit | When | Author | Message | Touched files |
|--------|------|--------|---------|---------------|
| `b57200c` | recent | Jesus Lara | feat(qsurl-parser): TASK-774 — QSUrlError pass-through | `querysource/outputs/output.py` (`DataOutput.response` body) |
| `24e5940` | recent | Jesus Lara | feat(per-tenant-queries): TASK-731 — implicit artifacts | `querysource/outputs/output.py` (`DataOutput.__init__`) |
| `5fcbba87` | 2025-04-02 | Jesus Lara | xml and pdf fixed outputs | `querysource/outputs/writers/pdf.py` |
| `a492560a` | 2025-02-12 | Jesus Lara | main revision of dependencies | `querysource/outputs/writers/__init__.py` |

The registry and the writer package have not changed since early 2025. The recent `output.py` edits touch
the `DataOutput` body, not the registry, so the change carries a low risk of merge conflicts (F008).

---

## 3. Probable Scope

### What's New

- **Lazy writer registry**: `WRITERS` values become `"module:ClassName"` specs, for example
  `'pdf': '.writers.pdf:PDFWriter'`. A small resolver imports the class on first lookup and
  caches it back into the mapping. Values that are already classes are returned as they are,
  which keeps the `monkeypatch.setitem` tests working.

### What Changes

- **`querysource/outputs/output.py`**: drop the top-level `from .writers import (...)`. Build
  `WRITERS` from specs, and resolve through the helper in `DataOutput.response`, keeping the
  json fallback for unknown keys. *Evidence*: F002, F007
- **`querysource/outputs/writers/__init__.py`**: replace the eager imports with a PEP 562
  `__getattr__` and a name→module map, so `from querysource.outputs.writers import X` still
  works. Keep `__all__` and the commented-out writers as they are. *Evidence*: F001
- **`querysource/outputs/__init__.py`**: expose `DataOutput` through a PEP 562 `__getattr__`,
  so that importing `querysource.outputs.dt` from `queries/base.py` stops loading `output.py`.
  *Evidence*: F006
- **`querysource/outputs/writers/pdf.py::PDFWriter.get_response`**: import `weasyprint.HTML`
  inside the method and run `write_pdf` with `await asyncio.to_thread(...)`, so it no longer
  blocks the event loop. *Evidence*: F004
- **Tests**: add a regression test that fails if importing `querysource.queries.qs` or
  `querysource.outputs.output` puts `weasyprint` into `sys.modules`. Run it in a subprocess
  for isolation. Add registry tests for lazy resolution, caching, class override and the
  unknown-format fallback. *Evidence*: F005, F007

### What's Untouched (Non-Goals)

- The WeasyPrint dependency stays in `[project].dependencies` (`pyproject.toml:111`). No
  packaging change (user decision U2).
- Other import-time costs such as asyncdb/BigQuery (~0.5 s) and pandas (~0.2 s) are
  separate. They could get a follow-up.
- `describe.py` / `eda.py` / `clustering.py` / `profiling.py` stay disabled and keep their
  imports as they are.
- Output destinations (`querysource/outputs/destinations/`) and the `outputs.dt` factory
  internals.

### Patterns to Follow

- Defer heavy imports to the call site, the way `bokeh.py` L37-41, `plotly.py` L79-80 and
  `report.py` L10-13 already do. *Evidence*: F003
- There is already lazy-import precedent in the repo: `querysource/queries/multi/destinations/__init__.py:37`
  imports `AbstractDestination` inside a function (`# noqa: PLC0415`). *Evidence*: F006

### Integration Risks

- **Import errors show up later**: a broken writer now fails on its first request instead of at
  startup. *Mitigation*: a unit test that resolves every registered spec.
- **Thread offload**: weasyprint rendering in a worker thread is safe (no shared event-loop
  state), but concurrent PDF requests now run in parallel threads and can use more memory.
  *Mitigation*: consider capping concurrency with a semaphore in the spec. *Evidence*: F004
- **Library callers** that rely on `querysource.outputs` pulling in `aiohttp` writers as a side
  effect. None were found in the repo. *Evidence*: F006

---

## 4. Confidence Map

| ID | Claim | Evidence | Confidence | Reasoning |
|----|-------|----------|------------|-----------|
| C1 | weasyprint is the only writer dependency with a material import cost; other writers take <3 ms | F003, F005 | high | measured with `-X importtime` and confirmed by grep of top-level imports |
| C2 | weasyprint loads in both library mode (`queries.qs`) and HTTP mode (`services`) before any PDF request | F006, F009 | high | import tree traced end to end |
| C3 | Writer classes are only needed in `DataOutput.response`, so they can be resolved later | F002 | high | grep shows no other consumers |
| C4 | `WRITERS` must stay a module-level mutable mapping that accepts classes | F007 | high | direct `monkeypatch.setitem` in tests |
| C5 | Moving the weasyprint import into `get_response` does not change behaviour | F004 | high | `HTML` is used only there |
| C6 | Expected saving is ~27% of `import querysource.queries.qs` on this machine | F005 | medium | three consistent local runs; production hosts may differ (cffi/pango load time) |
| C7 | `asyncio.to_thread` is enough to unblock the event loop during `write_pdf` | F004 | medium | standard pattern, but weasyprint thread safety under concurrency has not been verified here |

Distribution: **5** high, **2** medium, **0** low.

---

## 5. Open Questions

### Resolved (during proposal phase)

- [x] **Scope: PDF-only, lazy registry for all writers, or also decouple the outputs package?**
  *Resolved*: "Lazy registry, all". The package-level `__getattr__` for `outputs/__init__.py`
  is included because it is what stops the library path from loading `output.py` (F006).
  *Resolves claims*: C2, C3
- [x] **Keep WeasyPrint as a hard dependency or move it to an extra?** *Resolved*: "Keep hard
  dependency".
- [x] **Include the blocking `write_pdf` fix?** *Resolved*: "Include to_thread fix".
  *Resolves claims*: C7

### Unresolved (defer to spec / implementation)

- [ ] **Should PDF concurrency be capped (semaphore / dedicated executor) when rendering off-loop?**
  *Owner*: tbd. *Blocks claims*: C7.
  *Plausible answers*: a) the default `to_thread` pool is enough, b) a module-level semaphore
  (e.g. 4).

---

## 6. Recommended Next Step

**`/sdd-spec FEAT-154`**: *Rationale*: localization is complete and measured, the user
resolved every design choice, and the change stays inside `querysource/outputs/`.

### Alternatives

- **`/sdd-task FEAT-154`**: the change is small (4 source files plus tests), but the registry
  contract (C4) deserves a spec'd acceptance criterion first.
- **`/sdd-brainstorm FEAT-154`**: not needed; there is no architectural choice left open.

> **ID note**: this proposal was first drafted as FEAT-177 (max+1 scan). `/sdd-spec`
> then reserved **FEAT-154** through the ledger (`scripts/sdd/reserve_ids.py`), and the
> proposal and `sdd/state/` were renumbered to match, so the feature has a single ID.

---

## 7. Research Audit

| Artifact | Path |
|----------|------|
| State checkpoints | `sdd/state/FEAT-154/state.json` |
| Source (raw) | `sdd/state/FEAT-154/source.md` |
| Research plan | `sdd/state/FEAT-154/research_plan.json` |
| Findings (digests) | `sdd/state/FEAT-154/findings/F001-*.md` … `F009-*.md` |
| Synthesis (JSON) | `sdd/state/FEAT-154/synthesis.json` |

**Budget consumed** (default profile):
- Files read: 9 / 40
- Grep calls: 8 / 25
- Git calls: 2 / 10
- Depth: 1 / 2
- Truncated: **no**

**Mode determination**: `auto` → resolved to `enrichment` (performance improvement request,
no failure signal). The plan gate was not shown interactively (auto mode); the plan was persisted afterwards.

---

## 8. Provenance

| Field | Value |
|-------|-------|
| Generated by | `/sdd-proposal v1.0` |
| Synthesis prompt | `sdd/templates/synthesis.prompt.md v1.0` |
| Plan prompt | `sdd/templates/research_plan.prompt.md v1.0` |
| Schema versions | state=1.0, synthesis=1.0, research_plan=1.0 |
| Operator | Claude Code (Opus 5.5) for Jesus Lara |
