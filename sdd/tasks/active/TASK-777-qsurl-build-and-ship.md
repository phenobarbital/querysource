# TASK-777: Build and ship the `_qsurl` extension (Makefile, release.yml, package-data, uv.lock)

**Feature**: FEAT-152 — qsurl: HTSQL-style URL query dialect (chumsky 0.13 + PyO3)
**Spec**: `sdd/specs/qsurl-parser.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-764, TASK-765, TASK-766
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 11, AC2, AC17. The second native extension must be built, staged and
shipped exactly like `_qs_parsers`: `make build-rust` installs both into `.venv`,
`make stage-rust` copies both `.so` files into the source tree, `release.yml`'s
`CIBW_BEFORE_BUILD` builds and extracts both before `cibuildwheel` repairs the wheel, and
`package-data` ships `_qsurl*.so` plus `grammar.lark` (without which the Lark fallback
silently breaks in wheels). `lark>=1.3.1` is already committed as a direct dependency
(`pyproject.toml:119`); `uv.lock` must be regenerated to record it.

---

## Scope

- `Makefile`: `build-rust` runs maturin develop for both manifests; `stage-rust` stages both `.so` files.
- `.github/workflows/release.yml`: build + extract `_qsurl` in `CIBW_BEFORE_BUILD`.
- `pyproject.toml`: `package-data` for `querysource.qsurl`.
- `uv lock` → `uv.lock`.
- `tests/qsurl/test_wheel_layout.py`: grammar present as package data; extension importable when `HAS_RUST`.
- Run `make build-rust` and confirm `HAS_RUST` is True.

**NOT in scope**: changes to either crate's sources; a CI workflow for tests (none exists).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `Makefile` | MODIFY | Second maturin target in `build-rust` and `stage-rust` |
| `.github/workflows/release.yml` | MODIFY | Build/extract `_qsurl` in `CIBW_BEFORE_BUILD` |
| `pyproject.toml` | MODIFY | `package-data` for `querysource.qsurl` |
| `uv.lock` | MODIFY | Regenerated (`lark` direct) |
| `tests/qsurl/test_wheel_layout.py` | CREATE | Packaging checks |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from importlib.resources import files     # stdlib
import querysource.qsurl as qsurl          # TASK-765 (HAS_RUST)
```

### Existing Signatures to Use
```make
# Makefile
MATURIN := .venv/bin/maturin                                            # line 17
build-rust:                                                             # line 63
	$(MATURIN) develop --release --manifest-path rust/Cargo.toml       # line 64
RUST_WHEEL_OUT := target/wheels                                         # line 72
stage-rust:                                                             # line 73
	$(MATURIN) build --release -i python --manifest-path rust/Cargo.toml --out $(RUST_WHEEL_OUT)
	@whl=$$(ls -t $(RUST_WHEEL_OUT)/qs_parsers-*.whl | head -1); \
	  test -n "$$whl" || { echo "ERROR: maturin produced no wheel in $(RUST_WHEEL_OUT)"; exit 1; }; \
	  echo "Staging Rust extension from $$whl"; \
	  tmp=$$(mktemp -d); \
	  unzip -o -q "$$whl" -d "$$tmp"; \
	  find "$$tmp" -name '_qs_parsers*.so' -exec cp {} querysource/qs_parsers/ \; ; \
	  rm -rf "$$tmp"; \
	  ls -la querysource/qs_parsers/_qs_parsers*.so                      # lines 74-80
```

```yaml
# .github/workflows/release.yml:47-53
          CIBW_BEFORE_BUILD: >-
            curl https://sh.rustup.rs -sSf | sh -s -- -y &&
            export PATH=/root/.cargo/bin:$PATH &&
            pip install maturin &&
            cd {project}/rust && rm -rf target/wheels &&
            maturin build --release --manylinux off --interpreter python3 &&
            python3 -c "import zipfile,glob,os,shutil;whl=glob.glob('target/wheels/*.whl')[0];zf=zipfile.ZipFile(whl);so=[n for n in zf.namelist() if n.endswith('.so') and '_qs_parsers' in n][0];zf.extract(so,'/tmp/_qs');shutil.copy2(os.path.join('/tmp/_qs',so),'{project}/querysource/qs_parsers/')" &&
            cd {project}
```

```toml
# pyproject.toml
    "lark>=1.3.1",                                         # line 119 (committed)
[tool.setuptools.package-data]                             # line 193
"querysource.qs_parsers" = ["*.so", "*.pyd"]               # line 195
```

### Does NOT Exist
- ~~`.github/workflows/ci.yml`~~ — only `codeql-analysis.yml` and `release.yml`.
- ~~a Cargo workspace~~ — build the two manifests separately.
- ~~`uv run --with maturin`~~ — forbidden by the Makefile comment (installs into an ephemeral env).
- ~~`rust/qsurl/target/wheels` being shared with `rust/target/wheels`~~ — maturin writes per-manifest `target/`; `stage-rust` uses `--out $(RUST_WHEEL_OUT)` so both wheels land in the repo-level `target/wheels` — select by name prefix (`qsurl-*.whl` vs `qs_parsers-*.whl`).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "Makefile", "action": "MODIFY"},
    {"path": ".github/workflows/release.yml", "action": "MODIFY"},
    {"path": "pyproject.toml", "action": "MODIFY"},
    {"path": "uv.lock", "action": "MODIFY"},
    {"path": "tests/qsurl/test_wheel_layout.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- **Exclusive task**: maturin installs into the shared `.venv` and `uv lock` rewrites `uv.lock`.
- The `release.yml` step must run from `{project}/rust/qsurl` and its extract one-liner must match `'_qsurl'` (not `'_qs_parsers'`) and copy into `{project}/querysource/qsurl/`.
- Never stage `.so` files into git (they are build artifacts; check `.gitignore` covers `querysource/qsurl/*.so` the same way it covers `querysource/qs_parsers/*.so` — FILL IN: verify and extend `.gitignore` only if the existing pattern does not already match; if you must edit `.gitignore`, add it to this task's file table in the Completion Note).

---

## Implementation Blueprint

### Steps (in order)
1. Edit `build-rust` and `stage-rust` — *why*: AC2/AC17 developer path.
2. Edit `release.yml` — *why*: published wheels must contain the extension.
3. Add `package-data` — *why*: setuptools only packages pre-existing artifacts it is told about; `*.lark` is needed by the fallback.
4. `source .venv/bin/activate && uv lock` — *why*: record `lark` as direct.
5. `make build-rust && python -c "from querysource.qsurl import HAS_RUST; assert HAS_RUST"` — *why*: AC2.
6. `make stage-rust` and confirm `querysource/qsurl/_qsurl*.so` exists (do not commit it).

### `Makefile` (MODIFY — build-rust)
```make
# occurrences: 1 (verified: grep -c '^build-rust:' Makefile)
# REPLACE the recipe of `build-rust:` (verified: Makefile:63-64) with:
build-rust:
	$(MATURIN) develop --release --manifest-path rust/Cargo.toml
	$(MATURIN) develop --release --manifest-path rust/qsurl/Cargo.toml
```

### `Makefile` (MODIFY — stage-rust)
```make
# occurrences: 1 (verified: grep -c '^stage-rust:' Makefile)
# APPEND to the end of the `stage-rust:` recipe (after `ls -la querysource/qs_parsers/_qs_parsers*.so`, Makefile:80):
	$(MATURIN) build --release -i python --manifest-path rust/qsurl/Cargo.toml --out $(RUST_WHEEL_OUT)
	@whl=$$(ls -t $(RUST_WHEEL_OUT)/qsurl-*.whl | head -1); \
	  test -n "$$whl" || { echo "ERROR: maturin produced no qsurl wheel in $(RUST_WHEEL_OUT)"; exit 1; }; \
	  echo "Staging qsurl extension from $$whl"; \
	  tmp=$$(mktemp -d); \
	  unzip -o -q "$$whl" -d "$$tmp"; \
	  find "$$tmp" -name '_qsurl*.so' -exec cp {} querysource/qsurl/ \; ; \
	  rm -rf "$$tmp"; \
	  ls -la querysource/qsurl/_qsurl*.so
```
**Why**: identical shape to the existing block so maintainers recognise it; tabs, not spaces, before recipe lines.

### `.github/workflows/release.yml` (MODIFY)
```yaml
# occurrences: 1 (verified: grep -c 'CIBW_BEFORE_BUILD: >-' .github/workflows/release.yml)
# INSIDE the `CIBW_BEFORE_BUILD: >-` scalar (release.yml:47-53): insert BEFORE the final `cd {project}` line:
            cd {project}/rust/qsurl && rm -rf target/wheels &&
            maturin build --release --manylinux off --interpreter python3 &&
            python3 -c "import zipfile,glob,os,shutil;whl=glob.glob('target/wheels/*.whl')[0];zf=zipfile.ZipFile(whl);so=[n for n in zf.namelist() if n.endswith('.so') and '_qsurl' in n][0];zf.extract(so,'/tmp/_qsurl');shutil.copy2(os.path.join('/tmp/_qsurl',so),'{project}/querysource/qsurl/')" &&
```

### `pyproject.toml` (MODIFY)
```toml
# occurrences: 1 (verified: grep -cF '"querysource.qs_parsers" = ["*.so", "*.pyd"]' pyproject.toml)
# AFTER — insert below `"querysource.qs_parsers" = ["*.so", "*.pyd"]` (verified: pyproject.toml:195)
"querysource.qsurl" = ["*.so", "*.pyd", "*.lark"]
```

### `uv.lock` (MODIFY)
```text
Regenerate with `uv lock` (venv active). Commit only if the diff is limited to recording lark as a
direct dependency; if unrelated packages move, report it in the Completion Note instead of committing blindly.
```

### `tests/qsurl/test_wheel_layout.py` (CREATE)
```python
"""Packaging: grammar ships as package data; the extension imports when built (spec AC17)."""
from __future__ import annotations

import importlib
from importlib.resources import files

import pytest

import querysource.qsurl as qsurl


def test_grammar_is_package_data():
    assert (files("querysource.qsurl") / "grammar.lark").is_file()


@pytest.mark.skipif(not qsurl.HAS_RUST, reason="qsurl Rust extension not installed")
def test_extension_exposes_parse_and_requires():
    # FILL IN: assert callable(qsurl._rs.parse) and callable(qsurl._rs.requires)
    ...


def test_package_data_declared_in_pyproject():
    # FILL IN: read pyproject.toml with tomllib; assert the querysource.qsurl entry lists "*.so", "*.pyd", "*.lark"
    ...
```

### FILL IN checklist
- [ ] `.gitignore` coverage check for staged `.so`.
- [ ] `uv.lock` diff review.
- [ ] Two test bodies.

---

## Acceptance Criteria

- [ ] `make build-rust` installs both extensions; `HAS_RUST` True afterwards (AC2).
- [ ] `make stage-rust` leaves `querysource/qsurl/_qsurl*.so` (not committed) (AC17).
- [ ] `release.yml` builds and extracts `_qsurl` like `_qs_parsers` (AC17).
- [ ] `package-data` includes `"querysource.qsurl" = ["*.so", "*.pyd", "*.lark"]`; `uv.lock` records `lark` (AC17).

---

## Validation Commands

- `pytest tests/qsurl/test_wheel_layout.py -q`

---

## Test Specification

See the `tests/qsurl/test_wheel_layout.py` block above.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at the path listed above for full context.
2. **Check dependencies** — verify every `Depends-on` task is `done` in `sdd/tasks/index/qsurl-parser.json`.
3. **Verify the Codebase Contract** before writing any code: re-run the `grep -c` for every MODIFY anchor; if a count changed, re-locate the anchor; if it is `0`, stop and report the drift.
4. **Update status** in `sdd/tasks/index/qsurl-parser.json` → `"in-progress"`.
5. **Implement** from the Implementation Blueprint; complete every `# FILL IN:`; never change a signature or path the blueprint fixes.
6. **Verify** every Acceptance Criterion and run the Validation Commands (`source .venv/bin/activate` first).
7. **Move this file** to `sdd/tasks/completed/TASK-777-qsurl-build-and-ship.md` and set the index entry to `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
