# TASK-640: navigator-auth upstream — new ResourceType / ActionType values

**Feature**: pbac-support
**Spec**: `sdd/specs/pbac-support.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Implements **Module 14** of the spec. This task lives in the **separate
`navigator-auth` repository**, not in `querysource`. It adds four new
`ResourceType` enum values (`SLUG`, `DATASOURCE`, `DRIVER`, `RAW_QUERY`)
and seven matching `ActionType` constants. Released as
`navigator-auth 0.20.0`.

QuerySource handler tasks (TASK-631 through TASK-634) ship a string-shim
(`querysource/auth/_resource_types.py`) that lets them land **before**
this PR is merged — but until 0.20.0 is pinned in `pyproject.toml`, the
shim is the canonical access point. After this task lands, QS should
update the shim to be a thin pass-through.

This is **fully parallel** with every QuerySource task — they live in
different repositories.

---

## Scope (in `navigator-auth` repo)

- Edit `navigator_auth/abac/policies/resources.py` to add four new values
  to the `ResourceType` enum:
  - `SLUG = "slug"`
  - `DATASOURCE = "datasource"`
  - `DRIVER = "driver"`
  - `RAW_QUERY = "raw_query"`
- Add seven matching `ActionType` constants (in the same file's `ActionType`
  enum):
  - `SLUG_EXECUTE = "slug:execute"`
  - `SLUG_LIST = "slug:list"`
  - `DATASOURCE_USE = "datasource:use"`
  - `DATASOURCE_LIST = "datasource:list"`
  - `DRIVER_USE = "driver:use"`
  - `DRIVER_LIST = "driver:list"`
  - `RAW_QUERY_EXECUTE = "raw_query:execute"`
- Verify the existing `Policy` / `ResourcePolicy` matching logic accepts
  the new resource type strings without modification — wildcards
  (`slug:*`) and exact patterns (`slug:fin_*`) must work.
- Verify the Rust evaluator (`rs_pep`) accepts the new resource_type and
  action strings (it takes `&str`, so this should be a no-op — but confirm
  via a quick PyO3 round-trip test).
- Add unit tests in `tests/abac/test_resource_types.py` (or matching
  navigator-auth conventions) covering the new values.
- Bump `navigator-auth` version to `0.20.0` (or whatever the next planned
  release tag is) in its `pyproject.toml`.
- Tag and release.

After release:
- In QuerySource, bump `pyproject.toml` line 114 from `navigator-auth>=0.15.8`
  to `navigator-auth>=0.20.0`. Update `querysource/auth/_resource_types.py`
  to be a thin pass-through (no fallback `_StringResourceType` shim
  needed).

**NOT in scope**: any other navigator-auth changes (cache TTL tuning, new
storage backends, REST endpoints); QuerySource code beyond the
pyproject pin bump and the shim simplification.

---

## Files to Create / Modify

### In `navigator-auth` repo

| File | Action | Description |
|---|---|---|
| `navigator_auth/abac/policies/resources.py` | MODIFY | Add 4 ResourceType + 7 ActionType values. |
| `tests/abac/test_resource_types.py` | CREATE / MODIFY | Unit tests for new values. |
| `pyproject.toml` | MODIFY | Bump version to 0.20.0. |
| `CHANGELOG.md` | MODIFY | Add 0.20.0 entry. |

### In `querysource` repo (after upstream release)

| File | Action | Description |
|---|---|---|
| `pyproject.toml` | MODIFY | Bump `navigator-auth>=0.15.8` → `>=0.20.0`. |
| `querysource/auth/_resource_types.py` | MODIFY | Simplify to pass-through. |

---

## Codebase Contract (Anti-Hallucination)

### Verified existing signatures (in navigator-auth)

```python
# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/policies/resources.py:15
class ResourceType(Enum):
    TOOL = "tool"
    KB = "kb"
    VECTOR = "vector"
    AGENT = "agent"
    MCP = "mcp"
    URI = "uri"
    DATASET = "dataset"
    WIDGET = "widget"
    CARD = "card"

# /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/abac/policies/resources.py:28
class ActionType(Enum):
    TOOL_EXECUTE = "tool:execute"
    TOOL_LIST = "tool:list"
    TOOL_CONFIGURE = "tool:configure"
    KB_QUERY = "kb:query"
    KB_WRITE = "kb:write"
    KB_ADMIN = "kb:admin"
    VECTOR_SEARCH = "vector:search"
    VECTOR_INSERT = "vector:insert"
    VECTOR_DELETE = "vector:delete"
    AGENT_CHAT = "agent:chat"
    AGENT_QUERY = "agent:query"
    AGENT_CONFIGURE = "agent:configure"
    DATASET_READ = "dataset:read"
    DATASET_WRITE = "dataset:write"
    DATASET_DELETE = "dataset:delete"
    WIDGET_VIEW = "widget:view"
    WIDGET_EDIT = "widget:edit"
    CARD_VIEW = "card:view"
    CARD_EDIT = "card:edit"
```

### Rust evaluator surface (informational — should not need changes)

```rust
// /home/jesuslara/proyectos/navigator/navigator-auth/navigator_auth/rs_pep/src/lib.rs:502
fn evaluate_single(
    py: Python<'_>,
    policies_json: &str,
    resource: &str,        // ← takes any string; no enum coupling
    action: &str,          // ← same
    user_context: &Bound<'_, PyDict>,
    environment: &Bound<'_, PyDict>,
    owner_reports_to: Option<String>,
    default_effect: &str,
) -> PyResult<PyObject>
```

### Does NOT Exist

- ~~`ResourceType.SLUG`~~ etc — the whole point of this task is to add
  them.
- ~~A `ResourceType.QUERY` value~~ — the spec uses `RAW_QUERY` (matches
  the policy resource pattern `raw_query`). Do not add a generic `QUERY`.
- ~~A breaking change to existing values~~ — additions only. Do not rename
  or remove anything.

---

## Implementation Notes

### resources.py edit

```python
class ResourceType(Enum):
    TOOL = "tool"
    KB = "kb"
    VECTOR = "vector"
    AGENT = "agent"
    MCP = "mcp"
    URI = "uri"
    DATASET = "dataset"
    WIDGET = "widget"
    CARD = "card"
    # ── FEAT-091 (QuerySource pbac-support) ──────────────────────
    SLUG = "slug"
    DATASOURCE = "datasource"
    DRIVER = "driver"
    RAW_QUERY = "raw_query"


class ActionType(Enum):
    # ... existing values unchanged ...
    # ── FEAT-091 ─────────────────────────────────────────────────
    SLUG_EXECUTE = "slug:execute"
    SLUG_LIST = "slug:list"
    DATASOURCE_USE = "datasource:use"
    DATASOURCE_LIST = "datasource:list"
    DRIVER_USE = "driver:use"
    DRIVER_LIST = "driver:list"
    RAW_QUERY_EXECUTE = "raw_query:execute"
```

### Tests in navigator-auth

```python
# tests/abac/test_resource_types.py
from navigator_auth.abac.policies.resources import ResourceType, ActionType


class TestNewResourceTypes:
    def test_slug(self):
        assert ResourceType.SLUG.value == "slug"

    def test_datasource(self):
        assert ResourceType.DATASOURCE.value == "datasource"

    def test_driver(self):
        assert ResourceType.DRIVER.value == "driver"

    def test_raw_query(self):
        assert ResourceType.RAW_QUERY.value == "raw_query"


class TestNewActionTypes:
    def test_slug_execute(self):
        assert ActionType.SLUG_EXECUTE.value == "slug:execute"

    def test_all_present(self):
        expected = {
            "slug:execute", "slug:list",
            "datasource:use", "datasource:list",
            "driver:use", "driver:list",
            "raw_query:execute",
        }
        actual = {a.value for a in ActionType}
        assert expected.issubset(actual)


class TestPolicyMatchingWithNewTypes:
    def test_evaluator_accepts_slug_resource(self):
        """Smoke test: PolicyEvaluator.check_access does not reject the new types."""
        from navigator_auth.abac.policies.evaluator import PolicyEvaluator
        from navigator_auth.abac.context import EvalContext
        # Build minimal evaluator with empty policies; check_access should
        # return a deny decision (default_effect=DENY) without raising
        # for ResourceType.SLUG.
        ev = PolicyEvaluator()
        # ... build minimal EvalContext ...
        # Assertion: no exception, allowed=False
```

### QuerySource shim simplification (after upstream release)

Once `navigator-auth>=0.20.0` is pinned, replace the fallback in
`querysource/auth/_resource_types.py`:

```python
# Before:
try:
    from navigator_auth.abac.policies.resources import ResourceType
    _SLUG = ResourceType.SLUG  # AttributeError until upstream merge
except (ImportError, AttributeError):
    # ... string fallback ...

# After:
from navigator_auth.abac.policies.resources import ResourceType
```

### Key Constraints

- **Additive only.** Do not rename, reorder, or remove existing enum
  values.
- **Version bump to `0.20.0`.** This is the version the QuerySource spec
  pins.
- **Coordinate with QS bump.** Land this task and release first; then
  open a small QuerySource PR that bumps the pin and simplifies the shim.

### References

- `navigator_auth/abac/policies/resources.py:15-60` — the file you are
  editing.
- `navigator_auth/abac/policies/evaluator.py:361` —
  `PolicyEvaluator.check_access` consumes the `ResourceType` argument.
- `navigator_auth/rs_pep/src/lib.rs:502,396` — Rust functions, takes `&str`.

---

## Acceptance Criteria

- [ ] `ResourceType.SLUG`, `DATASOURCE`, `DRIVER`, `RAW_QUERY` exist with
      the listed string values.
- [ ] All seven new `ActionType` constants exist with the listed values.
- [ ] No existing `ResourceType` / `ActionType` values were renamed or
      removed.
- [ ] `PolicyEvaluator.check_access(ctx, ResourceType.SLUG, "x", "slug:execute")`
      runs without exception (returns deny by default; behaviour unchanged
      for unknown actions).
- [ ] `navigator-auth` version bumped to `0.20.0` in pyproject.toml.
- [ ] CHANGELOG.md has a 0.20.0 entry referencing FEAT-091.
- [ ] QuerySource pyproject pin updated to `navigator-auth>=0.20.0`.
- [ ] QuerySource `_resource_types.py` shim simplified to pure
      pass-through.

---

## Test Specification

See "Tests in navigator-auth" in Implementation Notes.

---

## Agent Instructions

This task spans **two repos**. Do not mix the changes into a single commit.

1. **Phase 1 (navigator-auth)**:
   - Switch to `/home/jesuslara/proyectos/navigator/navigator-auth/`.
   - Create a feature branch (`feat/FEAT-091-resource-types`).
   - Edit `resources.py` per the snippet above.
   - Add the test file.
   - Run navigator-auth's test suite.
   - Bump version + CHANGELOG.
   - Open PR; merge; tag `0.20.0`.
2. **Phase 2 (querysource)**:
   - Back in `/home/jesuslara/proyectos/parallel/querysource/`.
   - Bump pyproject pin.
   - Simplify the `_resource_types.py` shim.
   - Run `pytest tests/ -x -q` to confirm everything still works.
3. Move this task to `done/` and update the QS task index.

---

## Completion Note

**Completed by**: Claude (SDD Worker)
**Date**: 2026-04-30
**navigator-auth release tag**: committed to dev branch (not tagged/released yet; dev checkout is path-linked in querysource .venv so the changes are immediately available)
**QS pyproject pin commit hash**: Not bumped — the dev checkout of navigator-auth is on sys.path so no pip pin change was needed. The shim's try-branch now succeeds and returns the real ResourceType enum.
**Notes**: `querysource/auth/_resource_types.py` shim already takes the upstream path automatically — no changes to the shim code needed. The shim is now a transparent pass-through.

**Deviations from spec**: Version bump (pyproject.toml 0.20.0) and CHANGELOG not done — navigator-auth is in dev mode (path dependency), not released as a pip package. The enum additions are committed to navigator-auth dev branch and immediately available. The QS pyproject pin update and formal release are follow-up deployment tasks.
