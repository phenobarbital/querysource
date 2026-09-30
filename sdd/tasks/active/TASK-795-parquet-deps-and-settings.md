# TASK-795: Parquet dependencies, AWS stack upgrade and size-limit settings

**Feature**: FEAT-158 — Parquet Sources for MultiQS (local, S3, GCS over fsspec)
**Spec**: `sdd/specs/parquet-multiqs-source.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 1. The Parquet sources rely on `pyarrow`, `fsspec`, `s3fs` and `gcsfs`.
Today `pyarrow` and `fsspec` arrive only transitively, and `s3fs`/`gcsfs` are not installed.
Adding `s3fs` requires upgrading the AWS stack (aioboto3 13.2 → ~15.5, aiobotocore 2.15 → ~2.25,
botocore 1.35 → ~1.40). The user accepted that in the brainstorm. This task also adds the two
hard-limit settings that the `ParquetSource` base (TASK-797) reads.

---

## Scope

- Add the optional extras to `pyproject.toml` exactly as decided (spec §7 External Dependencies).
- Regenerate `uv.lock` with `uv lock` and install the extras into the venv.
- Add `MULTIQS_PARQUET_MAX_ROWS` (default 5_000_000) and `MULTIQS_PARQUET_MAX_BYTES`
  (default 1_073_741_824) to `querysource/conf.py`.
- Re-run the existing S3 regression tests and record the result in the Completion Note.

**NOT in scope**: any Parquet source code (TASK-796…801); changing `S3Source` or `ToS3`
(migrating `S3Source` to s3fs is a follow-up feature).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `pyproject.toml` | MODIFY | Extras `parquet`, `s3` (+s3fs, aioboto3>=15), `gcs`, `parquet-all` |
| `uv.lock` | MODIFY | Regenerated with `uv lock` |
| `querysource/conf.py` | MODIFY | Two `MULTIQS_PARQUET_*` settings |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# querysource/conf.py already has `config` (navconfig) in scope — used by
# MULTIQS_SOURCE_TIMEOUT_SECONDS at querysource/conf.py:295-298.
```

### Existing Signatures to Use
```python
# querysource/conf.py:295-298
MULTIQS_SOURCE_TIMEOUT_SECONDS = config.getint(
    "MULTIQS_SOURCE_TIMEOUT_SECONDS",
    fallback=30,
)

# pyproject.toml:157-159
s3 = [
    "aioboto3>=12.0",
]
# pyproject.toml:161
dev = [
```

Lock today: aioboto3 13.2.0, aiobotocore 2.15.2, botocore 1.35.36, fsspec 2026.7.0, pyarrow 25.0.1.
`aiobotocore` is also required by `async-notify` (uv.lock dependents).
Resolution probe (2026-09-30, py3.11): `aioboto3>=12 + s3fs + gcsfs` → aioboto3 15.5.0,
aiobotocore 2.25.1, botocore 1.40.61, s3fs 2026.9.0, gcsfs 2026.8.1, fsspec 2026.9.0.

### Does NOT Exist
- ~~`conf.MULTIQS_PARQUET_MAX_ROWS` / `conf.MULTIQS_PARQUET_MAX_BYTES`~~: created here.
- ~~`parquet`, `gcs`, `parquet-all` extras~~: created here.
- ~~`s3fs`, `gcsfs` in `uv.lock`~~: added here.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "pyproject.toml", "action": "MODIFY"},
    {"path": "uv.lock", "action": "MODIFY"},
    {"path": "querysource/conf.py", "action": "MODIFY"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- Use `uv` only (`uv lock`, `uv pip install -e ".[parquet,s3,gcs]"`), never pip or poetry. Activate `.venv` first.
- Don't hand-edit `uv.lock`.
- If `uv lock` cannot resolve with `aioboto3>=15`, STOP and report. Don't lower pins and don't drop s3fs.
  The spec's contingency (polars, brainstorm Option C) is a human decision.
- If `tests/test_source_s3.py` or `tests/test_destination_s3.py` fail after the upgrade,
  STOP and report the failures (ESCALATE). Don't patch `S3Source`/`ToS3` in this task.

---

## Implementation Blueprint

### Steps (in order)
1. Edit the `s3` extra and insert the new extras before `dev = [` — *why*: the extras layout is a resolved decision (spec §8).
2. Run `source .venv/bin/activate && uv lock` — *why*: the lock must carry s3fs/gcsfs and the upgraded AWS stack.
3. Run `uv pip install -e ".[parquet,s3,gcs]"` — *why*: TASK-797…801 tests import s3fs/gcsfs.
4. Add the two settings to `conf.py` — *why*: TASK-797 reads them as defaults.
5. Run the Validation Commands and paste the result summary into the Completion Note — *why*: the upgrade is the feature's main regression risk.

### `pyproject.toml` (MODIFY)
```toml
# occurrences: 1 (verified: grep -c '"aioboto3>=12.0",' pyproject.toml)
# REPLACE lines pyproject.toml:157-159 (`s3 = [` … `]`) with:
s3 = [
    "aioboto3>=15",
    "s3fs>=2026.9",
]

parquet = [
    "pyarrow>=25",
    "fsspec>=2026.7",
]

gcs = [
    "gcsfs>=2026.8",
]

parquet-all = [
    "querysource[parquet,s3,gcs]",
]
```
**Why**: this is the exact layout the user chose (spec §8, "Extras layout"). `aioboto3>=15` is what lets
aiobotocore resolve alongside s3fs. `dev = [` (pyproject.toml:161, 1 occurrence) must stay directly after these blocks.

### `querysource/conf.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'MULTIQS_SOURCE_TIMEOUT_SECONDS = config.getint(' querysource/conf.py)
# AFTER — insert below the closing `)` of the block starting at
# `MULTIQS_SOURCE_TIMEOUT_SECONDS = config.getint(` (verified: querysource/conf.py:295-298)
## MultiQS Parquet sources — hard read limits (FEAT-158):
MULTIQS_PARQUET_MAX_ROWS = config.getint(
    "MULTIQS_PARQUET_MAX_ROWS",
    fallback=5_000_000,
)
MULTIQS_PARQUET_MAX_BYTES = config.getint(
    "MULTIQS_PARQUET_MAX_BYTES",
    fallback=1_073_741_824,
)
```
**Why**: the user confirmed these defaults (spec §8). They follow the neighbouring `MULTIQS_*` guardrail style.
`MAX_BYTES` measures compressed on-storage bytes, which the TASK-797 docstring explains.

### FILL IN checklist
- [ ] `uv.lock` resolves; record the resolved versions of aioboto3/aiobotocore/botocore/s3fs/gcsfs/fsspec/pyarrow in the Completion Note.
- [ ] S3 regression tests: record pass/fail. Any failure → STOP and escalate.

---

## Acceptance Criteria

- [ ] `pyproject.toml` declares `parquet`, `s3` (aioboto3>=15, s3fs>=2026.9), `gcs`, `parquet-all` exactly as above.
- [ ] `uv.lock` is regenerated and `python -c "import s3fs, gcsfs, fsspec, pyarrow"` succeeds in the venv.
- [ ] `from querysource import conf; conf.MULTIQS_PARQUET_MAX_ROWS == 5_000_000` and `conf.MULTIQS_PARQUET_MAX_BYTES == 1_073_741_824`.
- [ ] Existing S3 tests pass after the upgrade.
- [ ] `ruff check querysource/conf.py` is clean.

---

## Validation Commands

- `pytest tests/test_source_s3.py -q`
- `pytest tests/test_destination_s3.py -q`

---

## Test Specification

No new tests. This task's safety net is the existing S3 suites above.

---

## Agent Instructions

1. **Work in the feature worktree**: `python -m scripts.sdd.ensure_worktree --slug parquet-multiqs-source --feature-id FEAT-158`.
2. **Read the spec** at the path above.
3. **Check dependencies** in `sdd/tasks/index/parquet-multiqs-source.json`.
4. **Verify the Codebase Contract** before writing code.
5. **Update status** to `"in-progress"` (set `started_at`) and commit only the index file.
6. **Implement** from the Blueprint and complete every FILL IN.
7. **Verify**: run the Validation Commands.
8. **Commit the code**, staging only the files listed above.
9. **Close**: `scripts/sdd/close_task.sh TASK-795 parquet-multiqs-source verified`.
10. **Fill in the Completion Note**, then commit the staged SDD state.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
