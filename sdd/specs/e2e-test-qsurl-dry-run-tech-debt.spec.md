---
# SDD flow type and base branch (FEAT-145).
type: feature
base_branch: dev
projects: [tests, qsurl]
tags: [tests, docs, qsurl, tech-debt, sdd-fix]
---

# Feature Specification: Refresh stale "environment limitation" docstrings in qsurl tests

**Feature ID**: FEAT-169
**Date**: 2026-10-13
**Author**: Jesus Lara (spec drafted with Claude Code via /sdd-fix)
**Status**: approved
**Target version**: next patch
**Ledger issues**: `issue:4bd3bb62d420` (tech_debt, minor, discovered from spec:FEAT-152)

---

## 1. Motivation & Business Requirements

### Problem Statement

The module docstrings of `tests/handlers/test_qsurl_service.py` (TASK-775) and
`tests/e2e/test_qsurl_dry_run.py` (TASK-776) were written while the qsurl parser
back-ends were blocked in the authoring sandbox. They state that
`querysource.qsurl.parse()` "is therefore not functional here" and that
`querysource.qsurl._fallback` "does not exist yet", and the e2e docstring
explains the `HAS_RUST=False` monkeypatch as a workaround for a stale installed
binary.

Those statements are now false: `querysource/qsurl/_fallback.py` and the
`_qsurl` Rust extension both ship, and `qsurl.parse()` is functional (it raises
a structured `QSUrlError` on malformed input). The docstrings now mislead
readers about why the tests patch `parse` or build the IR by hand.

### Goals

- Rewrite both module docstrings so they describe the *current* design intent:
  - `test_qsurl_service.py` patches `querysource.handlers.qsurl.parse` to
    isolate the handler's own logic (source assembly, PBAC ordering, capability
    probe wiring, `translate.split` integration, error envelope) from the
    grammar, which has its own tests.
  - `test_qsurl_dry_run.py` builds the IR dict directly (the stable contract
    `parse()` produces) so the e2e composition `translate.split` → real dialect
    parsers → `QS.dry_run()` → `residual.apply` is tested independently of the
    grammar; `HAS_RUST=False` deliberately pins the Cython `pgSQLParser` path
    for the ILIKE pushdown case.
- Remove every "environment limitation", "blocked in this sandbox",
  "not functional here" and "does not exist yet" claim.

### Non-Goals (explicitly out of scope)

- Changing any test body, assertion, fixture or monkeypatch.
- Rewriting the tests to call the real `parse()`.

## 2. Architectural Design

Documentation-only change to two test module docstrings. No runtime code,
public interface or data model changes.

## 3. Module Breakdown

### Module 1: Test module docstrings

- `tests/handlers/test_qsurl_service.py` — module docstring (lines 1-14).
- `tests/e2e/test_qsurl_dry_run.py` — module docstring (lines 1-26).

## 4. Test Specification

No new tests. Both modules must still collect and pass unchanged:
`pytest tests/handlers/test_qsurl_service.py tests/e2e/test_qsurl_dry_run.py`.

## 5. Acceptance Criteria

- [ ] AC1: Neither module docstring contains "environment limitation",
      "blocked", "not functional" or "does not exist yet"
      (`grep -niE "environment limitation|blocked|not functional|does not exist yet"` on both files returns nothing).
- [ ] AC2: Each docstring states why the tests patch `parse` / build the IR
      directly as a deliberate isolation choice, and the e2e docstring states
      why `HAS_RUST` is forced to False.
- [ ] AC3: `pytest tests/handlers/test_qsurl_service.py tests/e2e/test_qsurl_dry_run.py`
      passes with the same test count as before the change.
- [ ] AC4: `git diff` touches only the two module docstrings.

## 6. Codebase Contract

### Verified Imports / Facts

- `querysource/qsurl/__init__.py:49` — `def parse(src: str) -> dict:` (functional).
- `querysource/qsurl/_fallback.py` exists (Lark fallback, TASK-766).
- `tests/e2e/test_qsurl_dry_run.py:97,115,126` — `monkeypatch.setattr(pgsql, "HAS_RUST", False)`.

### Does NOT Exist (Anti-Hallucination)

- No sandbox/environment blocker remains for the qsurl parser back-ends.

## 7. Implementation Notes & Constraints

- Keep each docstring's first summary line unchanged.
- Docstring-only edit: do not reformat surrounding code.
