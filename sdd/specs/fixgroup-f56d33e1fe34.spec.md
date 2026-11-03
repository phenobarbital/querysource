---
type: feature
base_branch: dev
projects: []
tags: [modin, packaging, dependencies]
---

# Feature Specification: Modin Extra Best-Effort Documentation and Pip Compatibility

**Feature ID**: FEAT-183
**Date**: 2026-11-03
**Author**: Jesus Lara
**Status**: approved
**Target version**: 5.3.0

---

## 1. Motivation & Business Requirements

> **Discovered-from**: `issue:73a622d58c0b`

### Problem Statement

The `[modin]` optional extra declares `modin>=0.37.1`, which pins `pandas<2.4`.
QuerySource requires `pandas>=3.0`. The `[tool.uv]` override-dependencies in
`pyproject.toml` forces `pandas>=3.0,<4` so that `uv lock` succeeds, but:

1. `pip install querysource[modin]` fails with a resolver conflict (modin
   requires `pandas<2.4`, querysource requires `pandas>=3.0`).
2. If a user force-installs, modin may raise an exception other than
   `ImportError` against pandas 3 — the runtime fallback already catches this
   (TASK-835), but no packaging-level guard prevents the broken install.
3. The extra is not documented as best-effort / uv-only.

### Goals

- G1: Document the `[modin]` extra as best-effort (uv-only) until upstream
  modin ships pandas 3 support.
- G2: Provide a clear pip-time signal that the extra is incompatible with the
  project's pandas floor, rather than a raw resolver error.
- G3: Verify the existing runtime fallback covers all import-time failure modes.

### Non-Goals (explicitly out of scope)

- Downgrading pandas to accommodate modin.
- Removing the `[modin]` extra entirely.
- Patching modin itself.

---

## Governing Decisions

decision audit not configured in this repository

---

## 2. Architectural Design

### Overview

No new modules. Changes are confined to:

- `pyproject.toml` — annotate the `[modin]` extra with a classifier/comment
  and add a conditional dependency marker that makes pip emit a clear message.
- `querysource/outputs/dt/modin.py` — add a module-level docstring noting
  best-effort status.
- `querysource/queries/multi/operators/abstract.py` — no changes needed
  (fallback already catches broad `Exception`).

### Key Design Decisions

1. **Keep the extra**: removing it would break existing `uv` installs that
   depend on it. The extra stays but is documented as best-effort.
2. **No environment marker trick**: PEP 508 markers cannot express "warn but
   install anyway"; the extra simply documents the constraint.
3. **Runtime guards already sufficient**: TASK-835 broadened the catches from
   `ImportError` to `Exception`. Verify this with an explicit test for
   `RuntimeError` from modin against pandas 3.

---

## 3. Implementation Plan

### Phase 1: Documentation & Packaging

- Update `pyproject.toml` comments on the `[modin]` extra to clearly state
  it is best-effort and uv-only.
- Add a `[modin-compat]` note or inline comment explaining the uv override.
- Update the module-level docstring in `querysource/outputs/dt/modin.py`.

### Phase 2: Verification

- Confirm existing tests in `tests/unit/test_pandas3_modin_fallback.py`
  cover both `ImportError` and `RuntimeError` paths.
- Add a test verifying that `AbstractOperator` also handles `RuntimeError`
  gracefully (already exists: `test_operator_modin_runtime_failure_falls_back`).

---

## 4. Acceptance Criteria

- **AC-1**: `pyproject.toml` `[modin]` extra has an inline comment stating
  best-effort status and the uv-override requirement.
- **AC-2**: `querysource/outputs/dt/modin.py` module docstring mentions
  best-effort and the pandas 3 incompatibility.
- **AC-3**: All existing modin fallback tests pass (`pytest tests/unit/test_pandas3_modin_fallback.py`).
- **AC-4**: No functional regression — `pytest tests/` passes on the affected modules.

### Validation Contract

tests:
- tests/unit/test_pandas3_modin_fallback.py
markers: none

## 5. Testing Strategy

- Unit tests: existing `tests/unit/test_pandas3_modin_fallback.py` covers
  the four paths (operator missing, operator runtime, modinFormat missing,
  modinFormat runtime).
- No new integration tests needed (documentation/comment change).

---

## 6. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Upstream modin ships pandas 3 support | Re-evaluate the override; remove best-effort label |
| Users ignore documentation | Runtime fallback already catches failures gracefully |
