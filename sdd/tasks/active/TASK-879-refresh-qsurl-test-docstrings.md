# TASK-879: Refresh stale environment-limitation docstrings in qsurl tests

**Feature**: FEAT-169 — Refresh stale "environment limitation" docstrings in qsurl tests
**Spec**: `sdd/specs/e2e-test-qsurl-dry-run-tech-debt.spec.md`
**Status**: pending
**Priority**: low
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned
**discovered_from**: issue:4bd3bb62d420

---

## Context

Ledger issue `issue:4bd3bb62d420` (spec §1): the module docstrings of the qsurl
handler and e2e tests still say the qsurl parser back-ends are blocked in the
sandbox and that `parse()` is not functional. Both back-ends now ship
(`querysource/qsurl/_fallback.py`, `_qsurl` Rust extension) and `parse()` works,
so the docstrings mislead readers about why the tests patch `parse` or build the
IR by hand. Implements spec §3 Module 1.

---

## Scope

- Rewrite the module docstring of `tests/handlers/test_qsurl_service.py`.
- Rewrite the module docstring of `tests/e2e/test_qsurl_dry_run.py`.
- Keep each docstring's first summary line verbatim.

**NOT in scope**: any test body, assertion, fixture or monkeypatch change; making
the tests call the real `parse()`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/handlers/test_qsurl_service.py` | MODIFY | Module docstring only |
| `tests/e2e/test_qsurl_dry_run.py` | MODIFY | Module docstring only |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
None — docstring-only change.

### Existing Signatures to Use
```python
# querysource/qsurl/__init__.py:49
def parse(src: str) -> dict:  # functional; raises QSUrlError on malformed input
```
- `tests/handlers/test_qsurl_service.py:1-14` — module docstring (NOTE TASK-775).
- `tests/e2e/test_qsurl_dry_run.py:1-26` — module docstring (NOTE TASK-776 + second limitation).
- `tests/e2e/test_qsurl_dry_run.py:97,115,126` — `monkeypatch.setattr(pgsql, "HAS_RUST", False)`.

### Does NOT Exist
- ~~any remaining sandbox blocker for the qsurl back-ends~~ — `_fallback.py` and `_qsurl*.so` ship.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "tests/handlers/test_qsurl_service.py", "action": "MODIFY"},
    {"path": "tests/e2e/test_qsurl_dry_run.py", "action": "MODIFY"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Replace the NOTE paragraph in `test_qsurl_service.py`'s docstring with the
   isolation rationale below — because the handler logic is under test, not the grammar.
2. Replace both NOTE paragraphs in `test_qsurl_dry_run.py`'s docstring with the
   IR-contract and Cython-pinning rationale below — because the e2e composition is
   under test and the `HAS_RUST=False` patch is now a deliberate path pin.
3. Run the validation commands; the test count must not change.

### `tests/handlers/test_qsurl_service.py` (MODIFY)
```python
# REPLACE the paragraph starting `NOTE (TASK-775, environment limitation):`
# occurrences: 1 (verified: grep -c 'environment limitation' tests/handlers/test_qsurl_service.py)
"""QSUrlService: 400 detail, PBAC ordering, ?q= form, decode-once (spec AC5-AC8).

Every test patches ``querysource.handlers.qsurl.parse`` on purpose: the qsurl
grammar has its own tests, so these isolate the handler's own logic (source
assembly, PBAC ordering, capability probe wiring, translate.split integration,
error envelope). ``translate.split`` itself (TASK-771) is real and unmocked
wherever a full IR is supplied.
"""
```
**Why**: states the deliberate design, not a past environment problem.

### `tests/e2e/test_qsurl_dry_run.py` (MODIFY)
```python
# REPLACE both NOTE paragraphs (TASK-776 + "Second environment limitation")
# occurrences: 1 (verified: grep -c 'environment limitation' tests/e2e/test_qsurl_dry_run.py)
# FILL IN: wording — must say the IR dict is built directly (the stable contract
#   parse() produces, spec §2 Data Models) so the composition split -> real dialect
#   parsers -> QS.dry_run() -> residual.apply is tested independently of the
#   grammar, and that HAS_RUST=False pins the Cython pgSQLParser path for the
#   ILIKE pushdown case — bounded by AC1/AC2.
```

### FILL IN checklist
- [ ] e2e docstring wording (AC1, AC2)

---

## Acceptance Criteria

- [ ] AC1: `grep -niE "environment limitation|blocked|not functional|does not exist yet"` on both files returns nothing.
- [ ] AC2: Docstrings explain the deliberate `parse` patching / IR construction and the `HAS_RUST=False` pin.
- [ ] AC3: Both modules pass with an unchanged test count.
- [ ] AC4: Diff touches only the two module docstrings.

## Validation Commands
- `pytest tests/handlers/test_qsurl_service.py -q`
- `pytest tests/e2e/test_qsurl_dry_run.py -q`

---

## Completion Note
