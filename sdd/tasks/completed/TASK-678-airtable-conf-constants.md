# TASK-678: Airtable env constants + `QS_AIRTABLE_OAUTH_ENABLED` flag in conf.py

**Feature**: FEAT-096 — Multi-Query ThreadSource: Airtable
**Spec**: `sdd/specs/multi-threadsource-airtable.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

The OAuth callback flow (`TASK-679`) and route registration (`TASK-680`) need a handful of env-backed constants. They live in `querysource/conf.py` next to every other navconfig-resolved setting. The `QS_AIRTABLE_OAUTH_ENABLED` flag gates whether the new routes are registered at all — defaults to `False` so existing deployments are unaffected.

This task has **no code dependencies** and can run in parallel with `TASK-674` / `TASK-675` / `TASK-676` if the team chooses. The downstream OAuth-related tasks (`TASK-679`, `TASK-680`) depend on it.

Implements §3 Module 5 (config half) of the spec.

---

## Scope

Append a new "Airtable Integration" block to `querysource/conf.py`:

```python
# ── Airtable Integration (FEAT-096) ───────────────────────────────────
# OAuth2 client credentials (server-side; never sent to the browser).
AIRTABLE_CLIENT_ID = config.get('AIRTABLE_CLIENT_ID')
AIRTABLE_CLIENT_SECRET = config.get('AIRTABLE_CLIENT_SECRET')

# Optional: default base id used when YAML does not specify one.
AIRTABLE_BASE_ID = config.get('AIRTABLE_BASE_ID')

# Server-wide Personal Access Token used when no user session is present.
# Per FEAT-096 §1 Non-Goals: a single global PAT only (no per-user PATs).
AIRTABLE_ACCESS_TOKEN = config.get('AIRTABLE_ACCESS_TOKEN')

# Must match the redirect URI registered with the Airtable OAuth2 app.
# Defaults assume QuerySource is reachable at its canonical /api/v1/qs/ path.
AIRTABLE_REDIRECT_URI = config.get(
    'AIRTABLE_REDIRECT_URI',
    fallback='http://localhost:5000/api/v1/qs/integrations/airtable/callback',
)

# Feature flag — when False (the default), the /connect and /callback
# routes are NOT registered by QuerySource.setup(). PAT-only operation
# of AirtableSource still works regardless.
QS_AIRTABLE_OAUTH_ENABLED = config.getboolean(
    'QS_AIRTABLE_OAUTH_ENABLED', fallback=False
)
```

Add a small test ensuring the names are importable at the expected path.

**NOT in scope**:
- Reading these constants from `services.py::QuerySource.setup` — `TASK-680`.
- Reading these constants from the OAuth views — `TASK-679`.
- Updating `pyproject.toml` for optional extras — not needed per Q-impl-1 (raw aiohttp, no new dep).
- Adding the values to a `.env.example` file — not part of the existing project convention (verified: no `.env.example` exists at repo root).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/conf.py` | MODIFY | Append the Airtable block at the end of the file (preserve existing order) |
| `tests/test_conf_airtable.py` | CREATE | Import-and-existence checks for the new constants |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
# querysource/conf.py header (verified, line 1-22 approx):
from navconfig import config       # the canonical settings reader
```

### Existing Signatures to Use

```python
# Pattern documented across querysource/conf.py:24-66 (verified):
DB_HOST = config.get('DBHOST', fallback='localhost')
POSTGRES_MIN_CONNECTIONS = config.getint('POSTGRES_MIN_CONNECTIONS', fallback=2)
POSTGRES_SSL = config.getboolean('POSTGRES_SSL', fallback=False)

# Use the same three call shapes:
#   config.get(KEY)                     → str | None
#   config.get(KEY, fallback=DEFAULT)   → str (or DEFAULT)
#   config.getboolean(KEY, fallback=False) → bool
#   config.getint(KEY, fallback=N)      → int

# Adjacent feature flag precedent:
# querysource/conf.py contains ENABLE_QS_SCHEDULER (verified usage at
# querysource/services.py:293) — a navconfig.getboolean(...) flag. Mirror
# the style.
```

### Does NOT Exist

- ~~`querysource.settings`, `querysource.config`~~ — settings live in `querysource/conf.py` only.
- ~~`os.environ.get(...)` directly in conf.py~~ — use `config.get(...)` (navconfig handles env, .ini, .env, etc.).
- ~~A typed `Settings` class~~ — `conf.py` is module-level constants, not a class. Do not introduce one for this feature.
- ~~`.env.example`~~ — not in repo (verified: `ls .env.example` returns no such file).

---

## Implementation Notes

### Pattern to Follow

Find the end of `querysource/conf.py` and append the block under a clearly-marked `# ── Airtable Integration (FEAT-096) ────────────` comment so it is greppable. Mirror the style of the existing PBAC block (which uses `# ── FEAT-091 PBAC ──` header — `grep -n "FEAT-091" querysource/conf.py` to find it).

### Key Constraints

- All six identifiers MUST be top-level module attributes (no nesting under classes / dicts).
- `QS_AIRTABLE_OAUTH_ENABLED` MUST default to `False`. Tests in `TASK-680` will rely on this default.
- The redirect URI default points to `http://localhost:5000/...` because that matches the standard local-dev pattern; production deployments override via env var.
- Do NOT introduce a typed wrapper (e.g. `AirtableConfig` dataclass) — flat constants are the existing convention.

### References in Codebase

- `querysource/conf.py:24-66` — examples of `config.get` / `getint` / `getboolean` usage.
- `querysource/services.py:293` — example of consuming a feature flag (`ENABLE_QS_SCHEDULER`).

---

## Acceptance Criteria

- [ ] `from querysource.conf import AIRTABLE_CLIENT_ID, AIRTABLE_CLIENT_SECRET, AIRTABLE_BASE_ID, AIRTABLE_ACCESS_TOKEN, AIRTABLE_REDIRECT_URI, QS_AIRTABLE_OAUTH_ENABLED` works.
- [ ] `QS_AIRTABLE_OAUTH_ENABLED` is `False` when the env var is unset (verified in a test that monkey-patches the env).
- [ ] `AIRTABLE_REDIRECT_URI` defaults to a string containing `/api/v1/qs/integrations/airtable/callback` when the env var is unset.
- [ ] No existing constants in `querysource/conf.py` were renamed or removed.
- [ ] `pytest tests/test_conf_airtable.py -v` passes.
- [ ] `ruff check querysource/conf.py tests/test_conf_airtable.py` passes.
- [ ] `grep "pat36EoFVW" .` returns zero matches (the leaked PAT must not be embedded anywhere as a default).

---

## Test Specification

```python
# tests/test_conf_airtable.py
import importlib

import pytest


def test_constants_importable():
    mod = importlib.import_module("querysource.conf")
    for name in (
        "AIRTABLE_CLIENT_ID",
        "AIRTABLE_CLIENT_SECRET",
        "AIRTABLE_BASE_ID",
        "AIRTABLE_ACCESS_TOKEN",
        "AIRTABLE_REDIRECT_URI",
        "QS_AIRTABLE_OAUTH_ENABLED",
    ):
        assert hasattr(mod, name), f"missing conf attr: {name}"


def test_oauth_disabled_by_default(monkeypatch):
    # navconfig reads from env first; with the var unset, default must be False.
    monkeypatch.delenv("QS_AIRTABLE_OAUTH_ENABLED", raising=False)
    import querysource.conf as conf
    importlib.reload(conf)
    assert conf.QS_AIRTABLE_OAUTH_ENABLED is False


def test_redirect_uri_default_contains_callback_path(monkeypatch):
    monkeypatch.delenv("AIRTABLE_REDIRECT_URI", raising=False)
    import querysource.conf as conf
    importlib.reload(conf)
    assert "/api/v1/qs/integrations/airtable/callback" in conf.AIRTABLE_REDIRECT_URI
```

---

## Agent Instructions

1. `grep -n "ENABLE_QS_SCHEDULER\|FEAT-091" querysource/conf.py` to find the style-precedent and the right insertion point (end of file is fine).
2. Append the Airtable block per Scope.
3. Run `pytest tests/test_conf_airtable.py -v`.
4. Move to `sdd/tasks/completed/` and update index.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**:
