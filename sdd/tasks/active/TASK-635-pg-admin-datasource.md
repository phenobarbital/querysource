# TASK-635: pg_admin datasource registration

**Feature**: pbac-support
**Spec**: `sdd/specs/pbac-support.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Implements **Module 9** of the spec. Registers a new `pg_admin` datasource
that uses the full-access `DB*` env-var prefix (vs. `postgres` which uses
read-only `PG_*`). Two distinct registered datasources allow per-tier
policies — `postgres` for normal users, `pg_admin` for admins — without any
runtime credential rewriting.

**Fully parallel** with all other tasks except TASK-636 (which needs this
file to exist) — touches a single new file plus a one-line registration in
`SUPPORTED`.

---

## Scope

- Create `querysource/datasources/drivers/pg_admin.py` modelled on
  `postgres.py` (29 lines). The class inherits from `pgDriver` (same base as
  `postgresDriver`); the difference is the credential set it loads at module
  import time.
- Register `pg_admin` in `SUPPORTED` at
  `querysource/datasources/drivers/__init__.py:29-114`.
- Wrap the module-level `pg_admin_default = pg_adminDriver(...)` instance
  creation in `try/except ValueError`, mirroring `postgres.py` line ~21:
  if `DB*` env vars aren't configured, set `pg_admin_default = None` so
  the driver-list handler quietly omits it instead of breaking startup.

**NOT in scope**: the `params_for(session)` hook (TASK-636); the
`credential_prefix` class attribute (TASK-636); any policy YAML referring
to `pg_admin` (TASK-639); driver factory plumbing (TASK-637).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/datasources/drivers/pg_admin.py` | CREATE | `pg_adminDriver` class + module-level `pg_admin_default` instance. |
| `querysource/datasources/drivers/__init__.py` | MODIFY | Register `pg_admin` in `SUPPORTED`. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
# querysource/datasources/drivers/pg_admin.py — to be written
from querysource.datasources.drivers.pg import pgDriver
from querysource.conf import (
    DBHOST, DBPORT, DBUSER, DBPWD, DBNAME, default_dsn,
)
```

### Existing Signatures to Use

```python
# /home/jesuslara/proyectos/parallel/querysource/querysource/datasources/drivers/postgres.py (29 lines)
# This is the structural template:
class postgresDriver(pgDriver):
    driver: str = 'postgres'
    name: str = 'postgres'
    defaults: str = asyncpg_url
try:
    postgres_default = postgresDriver(
        dsn=asyncpg_url,
        host=PG_HOST, port=PG_PORT, database=PG_DATABASE,
        user=PG_USER, password=PG_PWD,
    )
except ValueError:
    postgres_default = None

# /home/jesuslara/proyectos/parallel/querysource/querysource/datasources/drivers/pg.py:17
class pgDriver(SQLDriver):
    def params(self) -> dict:                                         # line 42
        return {
            "host": self.host, "port": self.port,
            "username": self.user, "password": self.password,
            "database": self.database,
        }

# /home/jesuslara/proyectos/parallel/querysource/querysource/datasources/drivers/__init__.py
SUPPORTED = {                                                          # line 29
    "postgres": {...}, "mysql": {...}, "oracle": {...}, ...           # 42 entries
}

# /home/jesuslara/proyectos/parallel/querysource/querysource/conf.py
# Full-access DB credentials (already defined):
DBHOST = config.get('DBHOST', fallback='localhost')                   # line 24
DBUSER = config.get('DBUSER')                                          # line 25
DBPWD  = config.get('DBPWD')                                           # line 26
DBNAME = config.get('DBNAME', fallback='navigator')                    # line 27
DBPORT = config.get('DBPORT', fallback=5432)                           # line 28
default_dsn = f'postgres://{DBUSER}:{DBPWD}@{DBHOST}:{DBPORT}/{DBNAME}'  # line 32
```

### Does NOT Exist

- ~~`pg_admin_default` instance~~ — this task creates it.
- ~~`pg_adminDriver` class~~ — this task creates it.
- ~~A "pg_admin" entry in `SUPPORTED`~~ — this task adds it.
- ~~`credential_prefix` class attribute on existing drivers~~ — TASK-636
  adds that. Do not pre-add it here; that would create a half-implemented
  surface that TASK-636 has to walk through.

---

## Implementation Notes

### `pg_admin.py` skeleton

```python
"""
pg_admin — PostgreSQL datasource using full-access DB* credentials.

Distinct registered datasource from `postgres` (which uses read-only
PG_* credentials). Policy gating decides which users can list/use this
datasource. See FEAT-091 (pbac-support).
"""
from querysource.datasources.drivers.pg import pgDriver
from querysource.conf import (
    DBHOST, DBPORT, DBUSER, DBPWD, DBNAME, default_dsn,
)


class pg_adminDriver(pgDriver):
    driver: str = 'pg_admin'
    name: str = 'pg_admin'
    defaults: str = default_dsn


try:
    pg_admin_default = pg_adminDriver(
        dsn=default_dsn,
        host=DBHOST,
        port=DBPORT,
        database=DBNAME,
        user=DBUSER,
        password=DBPWD,
    )
except ValueError:
    pg_admin_default = None
```

### Register in `SUPPORTED`

In `querysource/datasources/drivers/__init__.py`, follow the existing
registration style. Read the file first to determine the dict layout.
Typical entry shape (verify against actual file):

```python
"pg_admin": {
    "name": "PostgreSQL (Admin)",
    "driver": pg_adminDriver,
},
```

If the registration involves an import line at the top of `__init__.py`
(e.g., `from .postgres import postgresDriver`), add the parallel import:

```python
from .pg_admin import pg_adminDriver
```

### Key Constraints

- **`pg_admin_default = None` on missing credentials.** Existing deployments
  without `DB*` env vars must continue to start. The driver-list endpoint
  filters out None instances by convention (verify by reading
  `default_sources()` in `datasources/handlers/datasource.py:33`).
- **Same base class as `postgresDriver`.** Both inherit from `pgDriver`.
  Any per-driver behaviour difference is achieved via the `name` /
  `driver` class attrs, not via subclassing surgery.
- **Lower-case underscored class name (`pg_adminDriver`)** — matches
  `postgresDriver` style. Linters may complain; suppress per project
  convention (look at how `postgresDriver` handles it).

### References in Codebase

- `querysource/datasources/drivers/postgres.py` — exact template.
- `querysource/datasources/drivers/__init__.py:29-114` — registration site.
- `querysource/datasources/handlers/datasource.py:33` — confirm
  `default_sources()` skips drivers whose default instance is `None`. If
  it does NOT skip `None`, document and patch defensively.

---

## Acceptance Criteria

- [ ] `from querysource.datasources.drivers.pg_admin import pg_adminDriver`
      succeeds.
- [ ] `from querysource.datasources.drivers import SUPPORTED;
      assert "pg_admin" in SUPPORTED` passes.
- [ ] With `DB*` env vars set: `pg_admin_default` is a valid driver instance
      (not `None`).
- [ ] With `DB*` env vars **unset**: import does not raise;
      `pg_admin_default is None`.
- [ ] `pg_admin` appears in `default_sources()` output when its instance
      exists.
- [ ] No regressions: `pytest tests/ -x -q` clean.

---

## Test Specification

```python
# tests/datasources/test_pg_admin_registration.py
import pytest


class TestPgAdmin:
    def test_module_imports(self):
        from querysource.datasources.drivers.pg_admin import pg_adminDriver
        assert pg_adminDriver.driver == "pg_admin"

    def test_registered_in_supported(self):
        from querysource.datasources.drivers import SUPPORTED
        assert "pg_admin" in SUPPORTED

    def test_default_none_when_db_creds_missing(self, monkeypatch):
        # Force re-import with DB* unset:
        for k in ("DBHOST", "DBPORT", "DBUSER", "DBPWD", "DBNAME"):
            monkeypatch.delenv(k, raising=False)
        # Importing the module should not raise — try/except ValueError
        # protects pg_admin_default. Direct verification depends on
        # whether navconfig caches reads; document if monkeypatch isn't
        # sufficient and reduce to a structural check:
        import importlib
        from querysource.datasources.drivers import pg_admin
        importlib.reload(pg_admin)
        # Either the value is None or the driver was instantiated with
        # whatever fallbacks navconfig provided — both are acceptable
        # outcomes; what is NOT acceptable is an unhandled exception.
```

---

## Agent Instructions

1. Read spec sections 2 + 3 (Module 9) + 6.
2. Read `querysource/datasources/drivers/postgres.py` (29 lines) — the
   structural template.
3. Read `querysource/datasources/drivers/__init__.py:29-114` to determine
   the exact registration style.
4. Create `pg_admin.py` from the template above.
5. Register in `SUPPORTED`.
6. Add tests.
7. Run `pytest tests/ -x -q`.
8. Move task to `done/` and update index.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
