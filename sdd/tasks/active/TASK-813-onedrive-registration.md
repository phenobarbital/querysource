# TASK-813: Register OneDriveSource, `onedrive` extra, generated schema, docs

**Feature**: FEAT-159 — OneDrive Source for MultiQS
**Spec**: `sdd/specs/onedrive-multiqs-source.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-807, TASK-810
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 8. This task makes `OneDriveSource` dispatchable from the
`sources:` YAML, installable through an `onedrive` extra and documented. It
**shares files with FEAT-158 (parquet)**: the `sources/__init__.py` registry,
`pyproject.toml` extras, `tests/test_source_registry.py` and `generated/`.
Whichever lands second rebases.

---

## Scope

- `sources/__init__.py`: import, `__all__`, and a `"OneDriveSource"` literal entry in the registry.
- `pyproject.toml`: add the `onedrive = ["msgraph-sdk>=1.0", "azure-identity>=1.0", "httpx>=0.24"]` extra.
- Regenerate `generated/OneDriveSource.json` with `generate-multiquery-docs -o generated`.
- Tests: change the registry-count assertion to **membership** (so FEAT-158
  and FEAT-159 stop conflicting on a count), and add OneDrive assertions.
- `docs/PER_TENANT_QUERIES.md`: add a short "OneDrive source" usage section
  (modes table, the identity link at `/api/v1/user/identities/link/onedrive`,
  and the navigator-auth ≥0.29.0 requirement for delegated mode).

**NOT in scope**: enabling the navigator-auth backend and raising the pin
(M9, deferred until the navigator-auth 0.29.0 release).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/__init__.py` | MODIFY | register |
| `pyproject.toml` | MODIFY | `onedrive` extra |
| `generated/OneDriveSource.json` | CREATE | generated schema |
| `tests/test_source_registry.py` | MODIFY | membership instead of count |
| `tests/multi/sources/test_registry.py` | MODIFY | OneDrive entry |
| `docs/PER_TENANT_QUERIES.md` | MODIFY | usage section |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from .onedrive import OneDriveSource        # TASK-807
```

### Existing Signatures to Use
```text
querysource/queries/multi/sources/__init__.py:7   from .sharepoint import SharepointSource
querysource/queries/multi/sources/__init__.py:16      "SharepointSource",            (in __all__)
querysource/queries/multi/sources/__init__.py:31      "SharepointSource": SharepointSource,   (in SOURCE_REGISTRY)
pyproject.toml:151   sharepoint = [
tests/test_source_registry.py:35   assert len(SOURCE_REGISTRY) == 5
pyproject.toml:174   generate-multiquery-docs = "querysource.cli.generate_docs:main"
.pre-commit-config.yaml:15-17   hook runs `generate-multiquery-docs -o generated && git add generated`
```

### Does NOT Exist
- ~~`OneDriveSource.catalog.yaml`~~: not needed. The schema comes from introspection.
- ~~A `navigator-auth>=0.29.0` pin in this task~~: deferred (M9).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/sources/__init__.py", "action": "MODIFY"},
    {"path": "pyproject.toml", "action": "MODIFY"},
    {"path": "generated/OneDriveSource.json", "action": "CREATE"},
    {"path": "tests/test_source_registry.py", "action": "MODIFY"},
    {"path": "tests/multi/sources/test_registry.py", "action": "MODIFY"},
    {"path": "docs/PER_TENANT_QUERIES.md", "action": "MODIFY"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- **Exclusive task (`parallel: false`)**: it edits `pyproject.toml` and regenerates `generated/`.
- `uv.lock` is gitignored and untracked in this repository (.gitignore:275), so it is NOT
  part of this task: do not create, commit or relock it (corrected by the orchestrator).
- The generated `OneDriveSource.json` must list `auth`, `user`,
  `credentials.client_id|client_secret|tenant_id` and the shared `source.*` keys.

---

## Implementation Blueprint

### Steps (in order)
1. Register the source.
2. Add the extra, then run `uv lock` if needed.
3. Run `generate-multiquery-docs -o generated`.
4. Update the tests and docs.

### `querysource/queries/multi/sources/__init__.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'from .sharepoint import SharepointSource' …) — :7
# BEFORE — insert above it (keep alphabetical-ish order):
from .onedrive import OneDriveSource
# occurrences: 1 (verified: grep -c '    "SharepointSource",' …) — :16
# BEFORE:
    "OneDriveSource",
# occurrences: 1 (verified: grep -c '    "SharepointSource": SharepointSource,' …) — :31
# BEFORE:
    "OneDriveSource": OneDriveSource,
```

### `pyproject.toml` (MODIFY)
```toml
# occurrences: 1 (verified: grep -c 'sharepoint = [' pyproject.toml) — :151
# BEFORE — insert above it:
onedrive = [
    "msgraph-sdk>=1.0",
    "azure-identity>=1.0",
    "httpx>=0.24",
]
```

### `tests/test_source_registry.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '        assert len(SOURCE_REGISTRY) == 5' tests/test_source_registry.py) — :35
# REPLACE with:
        # Membership, not a count: parallel features (FEAT-158/159) each add sources.
        assert {"AirtableSource", "SharepointSource", "SmartSheetSource", "S3Source",
                "TableSource", "OneDriveSource"} <= set(SOURCE_REGISTRY)
```

### FILL IN checklist
- [ ] `tests/multi/sources/test_registry.py`: add `SOURCE_REGISTRY["OneDriveSource"] is OneDriveSource`
- [ ] docs usage section
- [ ] generated JSON committed

---

## Acceptance Criteria

- [ ] `SOURCE_REGISTRY["OneDriveSource"] is OneDriveSource` and `"OneDriveSource" in __all__`.
- [ ] `generated/OneDriveSource.json` exists with all mode fields.
- [ ] Registry tests pass with the membership assertion.

## Validation Commands

- `pytest tests/test_source_registry.py -q`
- `pytest tests/multi/sources/test_registry.py -q`
- `pytest tests/tenants/test_tenant_rollout_documentation.py -q`

---

## Test Specification

```python
# tests/multi/sources/test_registry.py (addition)
from querysource.queries.multi.sources import SOURCE_REGISTRY, OneDriveSource


def test_registry_contains_onedrive():
    assert SOURCE_REGISTRY["OneDriveSource"] is OneDriveSource
```

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug onedrive-multiqs-source --feature-id FEAT-159`).
2. Read the spec. Check that every `Depends-on` task is `"done"` in `sdd/tasks/index/onedrive-multiqs-source.json`.
3. Verify the Codebase Contract before writing code.
4. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
5. Run the Validation Commands. Commit only the listed files.
6. Close with `scripts/sdd/close_task.sh TASK-813 onedrive-multiqs-source verified`, then fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*
