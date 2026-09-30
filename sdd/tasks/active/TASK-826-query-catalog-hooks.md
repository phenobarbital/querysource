# TASK-826: Document `pre-hook` / `post-hook` in the `Query` catalog and regenerate `generated/Query.json`

**Feature**: FEAT-157 — MultiQuery Source Pre/Post-Hooks (PostgreSQL)
**Spec**: `sdd/specs/multi-source-hooks.spec.md`
**Status**: pending
**Priority**: low
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 4 and Goal "The MultiQuery `Query` catalog/JSON schema documents `pre-hook` and
`post-hook`". The `Query` component (class `ThreadQuery`) has no introspectable attributes. Its
catalog entry is built entirely from the companion file `query.catalog.yaml` and published under
`generated/`. This task adds the two attributes and regenerates the published JSON. It has no code
dependency on the other FEAT-157 tasks: it only touches documentation, and the registry reads the
YAML.

It is **exclusive** (`parallel: false`). `generate-multiquery-docs -o generated` rewrites the whole
shared `generated/` tree, so it must not run concurrently with another task that regenerates it.

---

## Scope

- Append the `pre-hook` and `post-hook` attributes under `attributes:` in `query.catalog.yaml`,
  directly after the `tenant` attribute and before the `# Schema-level constraints…` comment.
- Optionally extend the `usage:` prose with one sentence noting that `pre-hook` / `post-hook` are
  popped and never passed through as conditions. Do not change `schema:` or `example:`, because
  `tests/test_catalog_remote.py` asserts on the example and on `oneOf`.
- Regenerate `generated/Query.json` with `generate-multiquery-docs -o generated`.
  **Commit only `generated/Query.json`.** If the generator changes other files under `generated/`,
  they are pre-existing drift: restore them with `git checkout -- generated/<file>` and mention them
  in the Completion Note.
- Create `tests/test_catalog_hooks.py`, modelled on `tests/test_catalog_remote.py`.

**NOT in scope**: any runtime behaviour (TASK-823/824/825), and other components' catalogs.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/query.catalog.yaml` | MODIFY | Add `pre-hook` / `post-hook` attributes after `tenant` |
| `generated/Query.json` | MODIFY | Regenerated output |
| `tests/test_catalog_hooks.py` | CREATE | `test_catalog_lists_hooks` |

---

## Codebase Contract (Anti-Hallucination)

Re-verified against `dev` @ `f8a32ae`. There is no drift since the spec's `7337dbb`.

### Verified Imports
```python
from querysource.queries.multi.registry import ComponentRegistry   # verified: tests/test_catalog_remote.py:11
import json                                                        # stdlib (read generated/Query.json)
from pathlib import Path                                           # stdlib
```

### Existing Signatures to Use
```text
# querysource/queries/multi/sources/query.catalog.yaml
attributes:                                   # :34
  - name: tenant                              # :79  (last attribute; description ends at :87)
      Examples: ``null`` (legacy), ``client_a`` (explicit tenant).   # :87
# Schema-level constraints overlaid onto the synthesized json_schema.   # :89
schema: … additionalProperties: true          # :90-101
example: |-                                   # :103

# generated/Query.json — top-level keys: name, category, description, usage, attributes,
#   json_schema, example, icon; attributes today: slug, query, driver, datasource, remote, worker, tenant
#   json_schema.properties.<name> = {"type": "string", "description": …, "default": null} for type: str

# ComponentRegistry.get_catalog() → iterable of entries with .name, .attributes (each .name/.type/
#   .required/.default/.description), .json_schema, .example   (usage: tests/test_catalog_remote.py:14-18)

# CLI: pyproject.toml:174  generate-multiquery-docs = "querysource.cli.generate_docs:main"
#   installed at .venv/bin/generate-multiquery-docs; flags: -o/--output-dir, -c/--category, -f json|summary
#   .pre-commit-config.yaml:15-17 runs: bash -c 'generate-multiquery-docs -o generated >/dev/null && git add generated'
```

### Does NOT Exist
- ~~`pre-hook` / `post-hook` in `query.catalog.yaml` or `generated/Query.json`~~ before this task.
- ~~A `str | list` union type in the catalog attribute schema~~. Existing attributes use scalar
  `type:` values. Keep `type: str` as the spec skeleton does, and state "string or list of strings"
  in the description.
- ~~`ThreadQuery._catalog`~~: the documentation lives only in the companion YAML (TASK-698).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/sources/query.catalog.yaml", "action": "MODIFY"},
    {"path": "generated/Query.json", "action": "MODIFY"},
    {"path": "tests/test_catalog_hooks.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/registry.py#ComponentRegistry"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- The descriptions must state these points (spec §3 M4):
  - SQL string or list of strings;
  - PostgreSQL `queries` entries only (a slug on `db`/`pg` provider, or raw `driver: pg|postgres|postgresql` without `datasource`);
  - runs isolated on the write (`DB*`) credentials in its own transaction, with no transaction shared with the read;
  - requires the `datasource:use` grant on `pg_admin`;
  - guarded: `DROP`, `TRUNCATE`, `ALTER … DROP`, `DO`, `GRANT`/`REVOKE`, role DDL, `COPY … PROGRAM` and transaction control are rejected;
  - pre-hook failure skips the read;
  - post-hook runs after a successful **or empty** read, never after a failed one;
  - never accepted from request conditions.
- **Generator environment**: importing `querysource` resolves the navconfig logstash host. In a
  sandbox without DNS, the generator and pytest both fail at import with `socket.gaierror`. That is
  an environment problem, not a code error, so run them where the pre-commit hook normally runs.

---

## Implementation Blueprint

### Steps (in order)
1. Add the two attributes to the YAML. *Why*: the companion YAML is the single source of truth for `Query`.
2. Run `generate-multiquery-docs -o generated`, then `git status --short generated/`. *Why*: the published JSON must match the YAML, since pre-commit enforces freshness.
3. Keep only the `generated/Query.json` change and restore any unrelated drift. *Why*: minimal diff.
4. Write the test and run the Validation Commands.

### `querysource/queries/multi/sources/query.catalog.yaml` (MODIFY)
```yaml
# occurrences: 1 (verified: grep -c '  - name: tenant' querysource/queries/multi/sources/query.catalog.yaml) — :79
# AFTER — insert below the tenant description's last line
#   `      Examples: ``null`` (legacy), ``client_a`` (explicit tenant).` (:87, occurrences: 1)
#   and before the blank line + `# Schema-level constraints…` comment (:89):
  - name: pre-hook
    type: str
    required: false
    default: null
    description: >-
      SQL (a string or a list of strings) run BEFORE this query's retrieval,
      isolated on the write (DB*) credentials in its own transaction — never
      on, or in a transaction shared with, the read. PostgreSQL ``queries``
      entries only (slug on the ``db``/``pg`` provider, or raw ``driver``
      ``pg``/``postgres``/``postgresql`` without ``datasource``); requires the
      ``datasource:use`` grant on ``pg_admin``. Guarded: DROP, TRUNCATE,
      ALTER … DROP, DO, GRANT/REVOKE, role DDL, COPY … PROGRAM and transaction
      control are rejected. If it fails the query is not read. Never accepted
      from request conditions.
  - name: post-hook
    type: str
    required: false
    default: null
    description: >-
      # FILL IN: same shape as pre-hook — bounded by: runs AFTER a successful
      #   OR empty (no data / 204) retrieval, NOT after a failed one; the empty
      #   result is still reported as "no data"; same credentials, isolation,
      #   PostgreSQL-only, pg_admin grant, guard and request-condition rules.
```
**Why**: this is spec §3 M4. Do not quote the `FILL IN` comment into the YAML. Replace it with a
real `>-` paragraph, because a `#` line inside a folded scalar would become text.

### `generated/Query.json` (MODIFY)
```bash
# Regenerated, never hand-edited:
generate-multiquery-docs -o generated
git status --short generated/     # expect only: M generated/Query.json
```

### `tests/test_catalog_hooks.py` (CREATE)
```python
"""FEAT-157 — the Query catalog documents pre-hook / post-hook (TASK-826)."""
import json
from pathlib import Path

import pytest

from querysource.queries.multi.registry import ComponentRegistry

_GENERATED = Path(__file__).resolve().parent.parent / "generated" / "Query.json"


@pytest.fixture(scope="module")
def query_entry():
    catalog = {c.name: c for c in ComponentRegistry.get_catalog()}
    assert "Query" in catalog, "Query component missing from catalog"
    return catalog["Query"]


def test_catalog_lists_hooks(query_entry):
    attrs = {a.name: a for a in query_entry.attributes}
    for key in ("pre-hook", "post-hook"):
        assert key in attrs
        assert attrs[key].required is False
        assert attrs[key].default is None
        assert attrs[key].type == "str"
        assert key in query_entry.json_schema["properties"]
    # FILL IN: assert "PostgreSQL" in pre-hook description and "empty" (or "no data") in the
    #   post-hook description — bounded by the wording actually written in the YAML.


def test_generated_query_json_lists_hooks():
    data = json.loads(_GENERATED.read_text(encoding="utf-8"))
    names = [a["name"] for a in data["attributes"]]
    assert "pre-hook" in names and "post-hook" in names
    assert names.index("pre-hook") == names.index("tenant") + 1
```

### FILL IN checklist
- [ ] `query.catalog.yaml`: write the `post-hook` description, bounded by spec §2 Execution and §3 M4.
- [ ] `tests/test_catalog_hooks.py`: add the description assertions that match the final wording.

---

## Acceptance Criteria

- [ ] `ComponentRegistry` exposes `pre-hook` and `post-hook` on the `Query` entry, with `type: str`, not required and default `None`.
- [ ] `generated/Query.json` lists `pre-hook` and `post-hook`, right after `tenant`, and `json_schema.properties` contains both.
- [ ] Only `generated/Query.json` changed under `generated/`.
- [ ] `tests/test_catalog_remote.py` still passes, so the example and `oneOf` are unchanged.

## Validation Commands

- `pytest tests/test_catalog_hooks.py -q`
- `pytest tests/test_catalog_remote.py -q`

---

## Test Specification

See the `tests/test_catalog_hooks.py` block above (spec §4 M4 row `test_catalog_lists_hooks`).

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug multi-source-hooks --feature-id FEAT-157`). The FEAT-156 merge precondition applies to the feature as a whole. This task itself does not import `guarded_sql`.
2. Read the spec. This task has no `Depends-on`, but it is exclusive (`parallel: false`), so do not run it concurrently with another task that regenerates `generated/`.
3. Verify the Codebase Contract before writing.
4. Set the task to `"in-progress"` in `sdd/tasks/index/multi-source-hooks.json` (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
5. Run the Validation Commands. Commit only the listed files.
6. Close with `scripts/sdd/close_task.sh TASK-826 multi-source-hooks verified`, then fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
