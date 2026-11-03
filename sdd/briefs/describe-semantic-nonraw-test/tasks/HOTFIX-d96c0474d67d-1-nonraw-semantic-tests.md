# HOTFIX-d96c0474d67d-1: Non-raw tests for describe_slug derived.semantic

**Brief**: `sdd/briefs/describe-semantic-nonraw-test.brief.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context
Ledger issue:d96c0474d67d. The redaction gate is already in place (`querysource/queries/describe.py:216` and `:224`), but no test covers it.

## Scope
- Add a test that calls `describe_slug` with `DescribeGrants(raw=False, admin=False)` on the same semantic
  definition as `test_describe_semantic_derived`. Assert that `derived.semantic` equals exactly
  `{"format": "ossie", "definition_kind": "inline", "catalog": "/api/v1/semantic/orders/catalog"}`
  (it must contain no `connection` and no `governance_roles`) and that `query_raw` appears in `payload["redacted"]`.
- Add a test with invalid semantic YAML and a non-raw grant. Assert
  `derived.semantic == {"error": "invalid semantic resource"}`. Add a raw-grant counterpart that asserts the error text is the real one, not the generic string.
- Extract the shared MagicMock definition setup into a helper so it is not duplicated.

**NOT in scope**: any change to `querysource/queries/describe.py`.

## Files to Create / Modify
| File | Action | Description |
|---|---|---|
| `tests/handlers/test_semantic_catalog.py` | MODIFY | Add the non-raw and invalid-YAML tests |

## Codebase Contract (Anti-Hallucination)
```python
# querysource/queries/describe.py:204
def _semantic_summary(base: dict[str, Any], *, raw: bool) -> dict[str, Any]
# querysource/queries/describe.py:230 describe_slug(definition, grants, columns_link=..., vocabulary_link=...)
# DescribeGrants(raw: bool, admin: bool), with the existing usage at tests/handlers/test_semantic_catalog.py:52
```

## Acceptance Criteria
- [ ] `pytest tests/handlers/test_semantic_catalog.py -q` passes.
- [ ] Re-enabling the leak (removing `if raw:` at describe.py:224) makes the new test fail.
- [ ] `ruff check tests/handlers/test_semantic_catalog.py` is clean.

## Validation Commands
- `pytest tests/handlers/test_semantic_catalog.py -q`

## Completion Note
