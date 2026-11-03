---
kind: brief
slug: describe-semantic-nonraw-test
key: d96c0474d67d
base_branch: dev
source:
  type: ledger
  ref: issue:d96c0474d67d
  url: null
jira_issue_key: null
ledger_issue: issue:d96c0474d67d
created: "2026-11-03"
---

# Brief: Cover the non-raw redaction of describe_slug derived.semantic

## Problem
Ledger issue:d96c0474d67d (vulnerability, major, from FEAT-175): "describe_slug derived.semantic
leaks connection, roles and parse errors to non-raw callers ... test_describe_semantic_derived uses
raw=True so redaction is untested."

## Root cause
The original leak was that `_semantic_summary` read the unredacted `query_raw`, and it has already
been fixed on `dev` by commit 87f6f34. `connection` and `governance_roles` are now emitted only
`if raw:` (`querysource/queries/describe.py:224`), and the parser error text is replaced for non-raw
callers (`querysource/queries/describe.py:216`). `describe_slug` passes `grants.raw` at
`querysource/queries/describe.py:284`. The part still open is a test gap: the only test,
`tests/handlers/test_semantic_catalog.py:52`, runs with `DescribeGrants(raw=True, admin=True)`, so
nothing stops the gate from being removed later without a test failing.

## Research findings
- `_semantic_summary(base, *, raw)`: `querysource/queries/describe.py:204`. Its only caller is `describe_slug` (`:284`).
- `redact_payload` strips `query_raw` (RAW_FIELDS, `:34`) for non-raw callers.
- No production code changes are needed.

## Decisions
- Owner chose a test-only brief (2026-11-03). Close the ledger issue at /sdd-done.

## Fix plan
1. HOTFIX-d96c0474d67d-1: add non-raw tests for the valid-YAML and invalid-YAML paths.

## Validation commands
- `pytest tests/handlers/test_semantic_catalog.py -q`
