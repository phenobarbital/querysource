---
id: FEAT-095
title: MultiQuery Documentation System
type: feature
mode: enrichment
status: review
source:
  kind: inline
  jira_key: null
  summary: MultiQuery documentation system with base class normalization, CLI generation, HTTP API for listing and validation
base_branch: dev
confidence: high
research_state: sdd/state/FEAT-095/
---

# FEAT-095 — MultiQuery Documentation System

## §0 Origin

Inline feature request: build a comprehensive documentation and introspection system for all MultiQuery components (Operators, Transforms, Sources, Outputs). The system should provide:

- A unified base class (`AbstractMulti`) normalizing common methods across `AbstractTransform`, `AbstractOperator`, and `AbstractComponent`
- A reflection classmethod returning all public attributes for documentation
- Detailed docstrings on every concrete component
- A CLI command generating per-component documentation files
- An HTTP GET endpoint listing all supported components
- An HTTP POST endpoint validating MultiQuery pipeline definitions

**Scope note:** ThreadSource (FEAT-093) and AbstractDestination (FEAT-094) are in-flight and explicitly deferred from this feature. FEAT-095 focuses on the existing merged classes only.

## §1 Synthesis Summary

### Problem

Three abstract base classes (`AbstractTransform`, `AbstractOperator`, `AbstractComponent`) share 90%+ identical boilerplate code (async context manager, kwargs-based init, start/run/close lifecycle) but have no common parent. There are zero introspection methods, zero documentation-generation tools, and zero HTTP endpoints for component discovery or pipeline validation. Most concrete components lack docstrings.

### Approach

1. Create `AbstractMulti` as a unified base providing shared boilerplate + introspection
2. Refactor existing abstract classes to inherit from it
3. Add comprehensive docstrings to all concrete components
4. Build a `get_schema()` classmethod that produces both JSON Schema (draft-2020-12) and a simplified attribute dict
5. Create a CLI command for batch documentation generation
6. Add HTTP endpoints for component listing and pipeline validation

## §2 Codebase Findings

### §2.1 Localization

| File | Symbol | Role | Evidence |
|------|--------|------|----------|
| `querysource/queries/multi/transformations/abstract.py` | `AbstractTransform` | Transform base — no ABC parent, `colum_info()` debug helper | F001 |
| `querysource/queries/multi/operators/abstract.py` | `AbstractOperator(ABC)` | Operator base — `_print_info()` debug helper | F001 |
| `querysource/queries/multi/components/abstract.py` | `AbstractComponent(ABC)` | Component base — scaffolded, zero implementations | F001 |
| `querysource/queries/multi/__init__.py` | `MultiQS`, `get_operator_module()`, `get_transform_module()` | Pipeline orchestrator + dynamic component discovery | F002, F003 |
| `querysource/queries/multi/operators/*.py` | `Join`, `Concat`, `Melt`, `Merge`, `GroupBy`, `Info`, `Filter` | 7 concrete operators | F002 |
| `querysource/queries/multi/transformations/*.py` | `tPandas`, `tOrder`, `Map`, `correlation`, `crosstab`, `pivot`, `Forecast`, `GoogleMaps` | 8 concrete transforms | F002 |
| `querysource/queries/multi/sources/*.py` | `ThreadQuery`, `ThreadFile` | 2 source threads (no common base in main) | F002 |
| `querysource/outputs/tables/TableOutput/table.py` | `TableOutput` | Output orchestrator (multi-flavor DB write) | F002 |
| `querysource/services.py` | Route registration | HTTP route setup — aiohttp/Navigator | F003 |
| `querysource/__cli__.py` | `main()` | CLI entry — plain asyncio REPL, no framework | F003 |

### §2.2 Constraints

- **No CLI framework** exists in the project — `__cli__.py` is a plain asyncio REPL. FEAT-095 will need to choose a CLI approach (argparse is the lightest option, already in stdlib). [F003]
- **Component discovery** relies on filename convention (`get_operator_module(clsname)` does `import_module('.operators.<clsname>')`) — any registry for documentation must mirror or wrap this pattern. [F002]
- **AbstractTransform does not inherit from ABC** (inconsistency with the other two) — normalizing to `AbstractMulti` fixes this. [F001]
- **Debug helpers have inconsistent names**: `colum_info()` on Transform vs `_print_info()` on Operator/Component — should be unified. [F001]
- **`AbstractComponent` has zero concrete implementations** — it was scaffolded but never used. It should still inherit from `AbstractMulti` so it's ready when components are added. [F001]
- **FEAT-093 and FEAT-094 are not merged** — ThreadSource and AbstractDestination should NOT be modified by this feature. They can be integrated into AbstractMulti hierarchy in a follow-up after they land. [F005]

### §2.3 Recent History

- FEAT-093 (multiquery-new-sources): active in worktree, adds `ThreadSource` base class
- FEAT-094 (multiquery-destinations): active in worktree, adds `AbstractDestination` base class
- Both features modify `querysource/queries/multi/__init__.py` — FEAT-095 must coordinate carefully to avoid merge conflicts

## §3 Hypothesis / Scope

### Architecture

```
AbstractMulti (new unified base)
├── __init__(data, **kwargs)        # shared kwargs-to-attrs
├── async __aenter__ / __aexit__    # shared lifecycle
├── async start() / run() / close() # abstract lifecycle
├── _print_info(df)                 # unified debug helper
├── @classmethod get_schema()       # → JSON Schema + simplified dict
├── @classmethod get_description()  # → {name, description, usage, category, example}
└── @classmethod get_attributes()   # → [{name, type, default, required}, ...]

AbstractTransform(AbstractMulti)    # keeps transform-specific logic (backend, run validation)
AbstractOperator(AbstractMulti)     # keeps operator-specific logic (modin support)
AbstractComponent(AbstractMulti)    # keeps component-specific logic (currently minimal)
```

### Work Streams

#### WS-1: AbstractMulti base class
- Create `querysource/queries/multi/abstract.py` with `AbstractMulti(ABC)`
- Extract common boilerplate from the three existing abstract classes
- Add `get_schema()`, `get_description()`, `get_attributes()` classmethods
- `get_schema()` returns both a JSON Schema (draft-2020-12) dict AND a simplified attribute list
- Uses `typing.get_type_hints()` for type extraction; falls back to `Any` for untyped attributes

#### WS-2: Refactor existing abstract classes
- `AbstractTransform` → inherits `AbstractMulti`, keeps transform-specific logic
- `AbstractOperator` → inherits `AbstractMulti`, keeps operator-specific logic
- `AbstractComponent` → inherits `AbstractMulti`, keeps component-specific logic
- Unify debug helpers to `_print_info()`
- Fix `AbstractTransform` missing ABC inheritance (now via `AbstractMulti`)

#### WS-3: Comprehensive docstrings
- Add structured docstrings to all 7 operators and 8 transforms
- Format: description, usage summary, attribute list, JSON example
- Follow Google-style docstring convention (compatible with mkdocstrings if used later)

#### WS-4: CLI documentation generator
- Add `querysource/cli/generate_docs.py` (or extend `__cli__.py`)
- Uses `argparse` (stdlib, no new dependency)
- Discovers all component classes via the existing `get_operator_module`/`get_transform_module` + a new `get_all_components()` helper
- Calls `get_schema()` and `get_description()` on each class
- Writes per-component JSON files to `generated/` directory with:
  - Component Name, Description, Usage, Category
  - JSON Schema, Attributes list, JSON Example

#### WS-5: HTTP GET /api/v3/components
- New handler returning JSON array of all registered components with schemas
- Response includes: name, category (Sources/Destinations/Transformations/Operations/Components), description, attributes, json_schema
- Registered in `services.py` alongside existing v3 routes

#### WS-6: HTTP POST /api/v3/validate
- Accepts a MultiQuery pipeline JSON payload
- Performs **syntactic + structural** validation:
  - All referenced operator/transform names must be valid (exist as classes)
  - Required attributes for each step must be present
  - Attribute types must match declared types
  - Pipeline structure checks: sources must be defined, Join/Merge need 2+ inputs, operators reference valid data keys
- Returns: `{valid: bool, errors: [{step, field, message}]}`
- Does NOT perform semantic validation (column existence, data-type flow)

## §4 Confidence Map

| Claim | Confidence | Basis |
|-------|-----------|-------|
| Three abstract classes share 90%+ identical boilerplate → unifiable | **high** | F001 — direct code comparison |
| No unified base class exists anywhere | **high** | F001 — comprehensive grep |
| No introspection/documentation methods exist on any class | **high** | F001, F002 — zero classmethods found |
| Component discovery is by filename convention (importlib) | **high** | F002, F003 — `get_operator_module`/`get_transform_module` |
| aiohttp/Navigator is the web framework for new endpoints | **high** | F003 — `services.py` pattern |
| `typing.get_type_hints()` will work for attribute extraction | **medium** | Most attrs are set via `setattr` in `__init__`, not class-level annotations — may need to also inspect `__init__` kwargs |
| CLI can use argparse without new dependencies | **high** | F003 — stdlib, no existing CLI framework to conflict with |

## §5 Open Questions

- [x] **U1:** Schema format → **Resolved:** Both JSON Schema (draft-2020-12) AND simplified dict.
- [x] **U2:** Validation depth → **Resolved:** Syntactic + structural (no semantic/data-type flow).

## §6 Recommended Next Step

```
→ /sdd-spec FEAT-095
```

**Rationale:** High confidence across all localization points. The scope is well-defined with 6 clear work streams. The codebase has been thoroughly analyzed and no major unknowns remain. A spec will formalize the acceptance criteria and decompose into implementable tasks.

**Alternatives:**
- `/sdd-brainstorm FEAT-095` — if alternative architectures should be explored (e.g., Pydantic-based vs inspect-based introspection)
- `/sdd-task FEAT-095` — not recommended; this is too large for direct task decomposition without a spec

## §7 Research Audit

| Metric | Value |
|--------|-------|
| Files read | 32 |
| Grep queries | 18 |
| Git queries | 3 |
| Findings | 5 |
| Budget profile | default |
| Truncated | no |
| State directory | `sdd/state/FEAT-095/` |
