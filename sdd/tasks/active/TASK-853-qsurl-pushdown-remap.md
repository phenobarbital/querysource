# TASK-853: qsurl pushdown/residual remap onto the operator table + provider capabilities + docs (M10, behaviour half)

**Feature**: FEAT-180 — Partial-matching operators for where_cond / filter dict values
**Spec**: `sdd/specs/filter-with-partial-matching.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-840, TASK-851, TASK-852
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 10 (behaviour half), resolved Q3, AC15. After this task:

- `translate.split` pushes a text leaf as `{col: {<expression>: <raw value>}}` (no `%`, no
  escaping — the dialect builder escapes and quotes) when the provider declares
  `text_match`, the value is a `str`, and `validate_partial_match(col, expr, value,
  supports_regex=False)` does not raise. A `contains` operand shorter than 3 characters is
  **not** an error on the URL path: the leaf stays residual (qsurl's "unpushable ⇒
  residual" rule). `regex`/`iregex` stay residual (spec §8 Q4).
- `residual.py`: `contains`/`not_contains`/`startswith`/`endswith` become **case-sensitive**;
  the four `i*` forms are case-insensitive; `iregex` is `case=False` behind
  `_check_regex_safety`.
- `mysqlProvider`, `sqlserverProvider`, `bigqueryProvider` declare `text_match`.
  **Task-time correction of the spec §3 M10 skeleton**: add `TEXT_MATCH` only
  (`BaseProvider.capabilities | {TEXT_MATCH}` for mysql/sqlserver,
  `sqlProvider.capabilities | {TEXT_MATCH}` for bigquery). The skeleton also added
  `alias/sort/limit/offset` to mysql/sqlserver, which would widen qsurl pushdown beyond
  this feature.
- `docs/QSURL.md`: operator table, provider table, pushdown section and a migration note
  (`~` is now case-sensitive; use `~*`).

**Behaviour change for qsurl users** — documented, intended (requester decision Q3).

E2E coverage stays on PostgreSQL: `tests/e2e/test_qsurl_dry_run.py` resolves only the
`pg` provider through its catalog fixture. The other providers' declarations are pinned by
`tests/qsurl/test_provider_capabilities.py`; their rendering is covered by TASK-851.
(This narrows spec §4 `test_qsurl_dry_run_text_ops[provider]` to pg — recorded deviation.)

---

## Scope

- `translate.py`: replace `_TEXT_PATTERNS` with `_TEXT_OPS`, re-export `like_escape` from
  `querysource.parsers.partial_matching`, rewrite the text branch of `_leaf_pushdown`.
- `residual.py`: case rules for the eight text expressions and `iregex`.
- Three providers: import `qsurl_caps`, declare `capabilities`.
- `docs/QSURL.md` updates.
- Update `tests/qsurl/test_translate.py`, `tests/qsurl/test_residual.py`,
  `tests/qsurl/test_provider_capabilities.py`, `tests/e2e/test_qsurl_dry_run.py`.

**NOT in scope**: grammar (TASK-852); regex pushdown (spec §8 Q4).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/qsurl/translate.py` | MODIFY | table-operator pushdown with raw values |
| `querysource/qsurl/residual.py` | MODIFY | case-sensitive plain forms, i* forms, iregex |
| `querysource/providers/mysql.py` | MODIFY | declare text_match |
| `querysource/providers/sqlserver.py` | MODIFY | declare text_match |
| `querysource/providers/bigquery.py` | MODIFY | declare text_match |
| `docs/QSURL.md` | MODIFY | operators, providers, pushdown section, migration note |
| `tests/qsurl/test_translate.py` | MODIFY | new pushdown expectations |
| `tests/qsurl/test_residual.py` | MODIFY | case rules |
| `tests/qsurl/test_provider_capabilities.py` | MODIFY | rows for mysql/sqlserver/bigquery |
| `tests/e2e/test_qsurl_dry_run.py` | MODIFY | LIKE for contains, ILIKE for icontains |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase
> (re-verified on `dev` at `71316ad4`, 2026-10-07). The implementing agent MUST use these exact
> imports, class names, and method signatures. **DO NOT** invent, guess, or assume any import,
> attribute, or method not listed here. If you need something not listed, VERIFY it exists first.

### Verified Imports
```python
from ..parsers.partial_matching import like_escape, validate_partial_match   # TASK-840 (qsurl → parsers is the allowed direction)
from ..exceptions import ParserError                                           # querysource/exceptions.py:86
from . import capabilities as caps                                             # verified: querysource/qsurl/translate.py:6
from ..qsurl import capabilities as qsurl_caps                                 # verified: querysource/providers/sql.py:19, pg.py:13
from querysource.providers.mysql import mysqlProvider                          # imports cleanly (verified 2026-10-07)
from querysource.providers.sqlserver import sqlserverProvider
from querysource.providers.bigquery import bigqueryProvider
```

### Existing Signatures to Use
```python
# querysource/qsurl/translate.py
_TEXT_PATTERNS: dict[str, tuple[str, str, str]] = {...}                    # lines 12-18 (remove)
def like_escape(value: str) -> str                                         # lines 21-23 (replace by re-export)
def _leaf_pushdown(leaf: dict, capabilities: frozenset[str]) -> tuple[str, object] | None   # line 26
#    if expr in _TEXT_PATTERNS:                                            # line 69
#        if caps.TEXT_MATCH not in capabilities or not isinstance(value, str):
#            return None
#        op, prefix, suffix = _TEXT_PATTERNS[expr]
#        return col, {op: f"{prefix}{like_escape(value)}{suffix}"}        # lines 69-73
#    # "regex" and anything unrecognised is never pushed down.            # line 75

# querysource/qsurl/residual.py
_MAX_REGEX_PATTERN_LENGTH = 200; _check_regex_safety(pattern) -> None      # lines 35-52
def _leaf_mask(df, leaf) -> pd.Series                                      # text arms lines 95-109:
#    if expr == "contains":      ... str.contains(re.escape(value), case=False, na=False, regex=True)   # line 95
#    if expr == "not_contains":  ... case=False
#    if expr == "startswith":    ... .str.lower().str.startswith(value.lower(), na=False)
#    if expr == "endswith":      ... .str.lower().str.endswith(value.lower(), na=False)
#    if expr == "regex":         _check_regex_safety(value); ... case=True                 # line 105

# providers
class BaseProvider: capabilities: frozenset[str] = qsurl_caps.BASE          # providers/abstract.py:40-41
class sqlProvider: capabilities = qsurl_caps.BASE | {ALIAS, SORT, LIMIT, OFFSET}   # providers/sql.py:44
class mysqlProvider(BaseProvider):  ... __parser__ = SQLParser              # mysql.py:29, :42 (no qsurl import today)
class sqlserverProvider(BaseProvider): __parser__ = msSQLParser             # sqlserver.py:23, :28 (no qsurl import today)
class bigqueryProvider(sqlProvider): __parser__ = BigQueryParser            # bigquery.py:25, :32 (no qsurl import today)

# tests
# tests/qsurl/test_translate.py:46-47 — expectations {"ILIKE": "%5\\%\\_off%"} / {"NOT ILIKE": "%x%"}
# tests/qsurl/test_residual.py:25,27 — startswith "SAN" → [1, 2] (case-insensitive today); contains "a.b" → []
# tests/qsurl/test_provider_capabilities.py:13-46 — parametrized exact capability sets
# tests/e2e/test_qsurl_dry_run.py:96-122 — "city ILIKE '%san%'", "name ILIKE '%o''brien%'"
# docs/QSURL.md:88-92 operators table; :172-176 provider table; :178-191 pushdown text + example
```

### Does NOT Exist
- ~~`translate.like_escape` as its own implementation after this task~~ — it is a re-export.
- ~~a `regex` capability on any provider~~ — stays undeclared (spec §8 Q4).
- ~~e2e catalog entries for mysql/sqlserver/bigquery~~ — do not add them.

---

## Complexity Contract

> Declaration for deterministic complexity routing (FEAT-561). `targets` match the
> Files table exactly.

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/qsurl/translate.py",
      "action": "MODIFY"
    },
    {
      "path": "querysource/qsurl/residual.py",
      "action": "MODIFY"
    },
    {
      "path": "querysource/providers/mysql.py",
      "action": "MODIFY"
    },
    {
      "path": "querysource/providers/sqlserver.py",
      "action": "MODIFY"
    },
    {
      "path": "querysource/providers/bigquery.py",
      "action": "MODIFY"
    },
    {
      "path": "docs/QSURL.md",
      "action": "MODIFY"
    },
    {
      "path": "tests/qsurl/test_translate.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/qsurl/test_residual.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/qsurl/test_provider_capabilities.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/e2e/test_qsurl_dry_run.py",
      "action": "MODIFY"
    }
  ],
  "contract_symbols": [
    "sym:querysource/qsurl/translate.py#_leaf_pushdown",
    "sym:querysource/qsurl/translate.py#like_escape",
    "sym:querysource/qsurl/residual.py#_leaf_mask",
    "sym:querysource/providers/mysql.py#mysqlProvider",
    "sym:querysource/providers/sqlserver.py#sqlserverProvider",
    "sym:querysource/providers/bigquery.py#bigqueryProvider",
    "sym:querysource/parsers/partial_matching.py#validate_partial_match"
  ]
}
```

---

## Implementation Blueprint

> **CRITICAL — Executor-ready starting point.** Write each block below to its declared
> path nearly verbatim, then complete every `# FILL IN:` marker. Blocks were derived
> from the spec's Interface Skeletons and re-verified against the Codebase Contract
> above when this task was written. Never change a signature, class name, or file path
> the blueprint fixes.

### Steps (in order)
1. `translate.py` — *why*: one escaping site per dialect (builders), so qsurl must send raw values.
2. `residual.py` — *why*: in-memory results must match pushed-down results (case rules).
3. Providers — *why*: let mysql/sqlserver/bigquery push text leaves now that their builders render them.
4. Tests and docs; run the Validation Commands.

### `querysource/qsurl/translate.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '_TEXT_PATTERNS: dict[str, tuple[str, str, str]] = {' querysource/qsurl/translate.py) — line 12
# REPLACE lines 12-23 (the _TEXT_PATTERNS dict and the local like_escape) with:
from ..exceptions import ParserError
from ..parsers.partial_matching import like_escape, validate_partial_match  # like_escape re-exported (compat)

_TEXT_OPS: frozenset[str] = frozenset({
    "contains", "not_contains", "icontains", "not_icontains",
    "startswith", "istartswith", "endswith", "iendswith",
})
# (move the two imports up next to the other imports at lines 4-8 if ruff's import-order rule requires it)

# occurrences: 1 (verified: grep -c '    if expr in _TEXT_PATTERNS:' querysource/qsurl/translate.py) — line 69
# REPLACE lines 69-73 with:
    if expr in _TEXT_OPS:
        if caps.TEXT_MATCH not in capabilities or not isinstance(value, str):
            return None
        try:
            validate_partial_match(col, expr, value, supports_regex=False)
        except ParserError:
            return None  # e.g. contains shorter than 3 chars: evaluated in memory instead
        return col, {expr: value}
```

### `querysource/qsurl/residual.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    if expr == "contains":' querysource/qsurl/residual.py) — line 95
# REPLACE lines 95-103 (contains / not_contains / startswith / endswith arms) with:
    if expr in ("contains", "not_contains", "icontains", "not_icontains"):
        mask = col.astype("string").str.contains(
            re.escape(value), case=not expr.endswith("icontains"), na=False, regex=True
        )
        return ~mask if expr.startswith("not_") else mask
    # FILL IN: startswith / endswith case-sensitive (no lower()); istartswith / iendswith lower-cased
    # both sides — bounded by AC15
# occurrences: 1 (verified: grep -c '    if expr == "regex":' querysource/qsurl/residual.py) — line 105
# REPLACE `    if expr == "regex":` with `    if expr in ("regex", "iregex"):` and pass
# `case=(expr == "regex")` to str.contains (keep _check_regex_safety and the re.error handling)
```

### `querysource/providers/mysql.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    __parser__ = SQLParser' querysource/providers/mysql.py) — line 42
# AFTER — insert below line 42:
    #: FEAT-180: SQLParser renders the partial-matching operators (LIKE / LOWER() forms).
    capabilities = BaseProvider.capabilities | {qsurl_caps.TEXT_MATCH}
# and add `from ..qsurl import capabilities as qsurl_caps` next to `from ..parsers.sql import SQLParser` (line 22)
```

### `querysource/providers/sqlserver.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    __parser__ = msSQLParser' querysource/providers/sqlserver.py) — line 28
# AFTER — insert below line 28:
    #: FEAT-180: msSQLParser renders the partial-matching operators.
    capabilities = BaseProvider.capabilities | {qsurl_caps.TEXT_MATCH}
# and add `from ..qsurl import capabilities as qsurl_caps` next to `from ..parsers.sqlserver import msSQLParser` (line 16)
```

### `querysource/providers/bigquery.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    __parser__ = BigQueryParser' querysource/providers/bigquery.py) — line 32
# AFTER — insert below line 32:
    #: FEAT-180: BigQueryParser renders the partial-matching operators.
    capabilities = sqlProvider.capabilities | {qsurl_caps.TEXT_MATCH}
# and add `from ..qsurl import capabilities as qsurl_caps` next to `from .sql import sqlProvider` (line 22)
```

### `docs/QSURL.md` (MODIFY)
```markdown
<!-- occurrences: 1 (verified: grep -c '| `~` | `contains` | case-insensitive on every engine |' docs/QSURL.md) — line 88 -->
<!-- REPLACE lines 88-91 with: -->
| `~` | `contains` | case-sensitive (FEAT-180) |
| `~*` | `icontains` | case-insensitive |
| `!~` / `!~*` | `not_contains` / `not_icontains` | case-sensitive / case-insensitive |
| `^=` / `^=*` | `startswith` / `istartswith` | case-sensitive / case-insensitive |
| `$=` / `$=*` | `endswith` / `iendswith` | case-sensitive / case-insensitive |
<!-- and `=~*` → `iregex` next to the `=~` row (line 92) -->
<!-- FILL IN: provider table (lines 172-176) adds text_match to sqlProvider-family rows for mysql,
     sqlserver, bigquery; rewrite lines 178-191 (pushdown text + example → {'filter': {'city': {'contains': 'san'}}});
     add a "Migration (FEAT-180)" note: `~ !~ ^= $=` are now case-sensitive, use the `*` forms;
     a contains operand shorter than 3 characters is evaluated in memory. Keep every heading listed
     in tests/qsurl/test_docs.py:12-23 and keep all ```qsurl examples parseable — bounded by AC15 -->
```

### `tests/qsurl/test_translate.py` (MODIFY)
```python
# occurrences: 1 each (verified: grep -c) — lines 46-47:
#        (_leaf("a", "contains", "5%_off"), "a", {"ILIKE": "%5\\%\\_off%"}),
#        (_leaf("a", "not_contains", "x"), "a", {"NOT ILIKE": "%x%"}),
# REPLACE lines 46-47 with:
        (_leaf("a", "contains", "5%_off"), "a", {"contains": "5%_off"}),
        (_leaf("a", "not_contains", "xyz"), "a", {"not_contains": "xyz"}),
        (_leaf("a", "icontains", "san"), "a", {"icontains": "san"}),
        (_leaf("a", "istartswith", "S"), "a", {"istartswith": "S"}),
# FILL IN: new test — _leaf("a", "contains", "ab") on PG stays residual (plan.filter keeps the leaf);
# test_like_escape (line 176) keeps passing through the re-export — bounded by AC15
```

### `tests/qsurl/test_residual.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c) — line 25:
#        ({"column": "city", "expression": "startswith", "value": "SAN"}, [1, 2]),
# REPLACE line 25 with:
        ({"column": "city", "expression": "istartswith", "value": "SAN"}, [1, 2]),
        ({"column": "city", "expression": "startswith", "value": "SAN"}, []),
# FILL IN: verify the expected ids against the stores_df fixture (tests/qsurl/conftest.py:22) and add
# contains vs icontains and iregex cases — bounded by AC15
```

### `tests/qsurl/test_provider_capabilities.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c) — import at line 7 `from querysource.providers.cassandra import cassandraProvider`
# AFTER — insert below line 7:
from querysource.providers.bigquery import bigqueryProvider
from querysource.providers.mysql import mysqlProvider
from querysource.providers.sqlserver import sqlserverProvider
# occurrences: 1 (verified: grep -c) — line 37:
#        (cassandraProvider, {"select", "filter", "in_list", "null_check", "limit"}, False),
# AFTER — insert below line 37:
        (mysqlProvider, {"select", "filter", "in_list", "null_check", "text_match"}, True),
        (sqlserverProvider, {"select", "filter", "in_list", "null_check", "text_match"}, True),
        (
            bigqueryProvider,
            {"select", "filter", "in_list", "null_check", "alias", "sort", "limit", "offset", "text_match"},
            True,
        ),
# FILL IN: confirm residual_scan for the three providers (BaseProvider default True) — bounded by AC15
```

### `tests/e2e/test_qsurl_dry_run.py` (MODIFY)
```python
# occurrences: 1 each (verified: grep -c) — line 104 / line 122:
# REPLACE `    assert "city ILIKE '%san%'" in sql` (line 104) with:
    assert "city LIKE '%san%'" in sql
# REPLACE `    assert "name ILIKE '%o''brien%'" in sql` (line 122) with:
    assert "name LIKE '%o''brien%'" in sql
# BEFORE `async def test_or_filter_is_residual_on_pg():` (line 125, occurrences: 1) — add:
async def test_text_match_icontains_pg(monkeypatch):
    monkeypatch.setattr(pgsql, "HAS_RUST", False)
    ir = _ir(
        filter={"and": [{"column": "city", "expression": "icontains", "value": "san"}]},
        requires=["select", "filter", "text_match"],
    )
    sql, plan = await _render(ir, pgProvider.capabilities, pgProvider.residual_scan)
    assert plan.is_empty()
    assert "city ILIKE '%san%'" in sql
# FILL IN: update the module docstring's FEAT-152 ILIKE paragraph to say text leaves now push down as
# table operators (FEAT-180); add a too-short `contains` case that stays residual — bounded by AC15
```

### FILL IN checklist
- [ ] residual startswith/endswith/i* arms — bounded by AC15.
- [ ] docs sections — bounded by AC15 and `tests/qsurl/test_docs.py`.
- [ ] tests: `test_translate.py` rows 46-47 → `{"contains": "5%_off"}`, `{"not_contains": "xyz"}` (note: `"x"` is too short for not_contains → now residual; add that as its own case), plus `icontains`/`istartswith` rows and a too-short residual case; `test_residual.py` row 25 → `istartswith` keeps `[1, 2]` and a new `startswith "SAN"` row expects `[]` (verify against the fixture); `test_provider_capabilities.py` rows for the three providers; e2e `test_text_match_pushdown_pg` → `city LIKE '%san%'`, quote test → `name LIKE '%o''brien%'`, new `icontains` → `city ILIKE '%san%'` — bounded by AC15.

---

## Acceptance Criteria

- [ ] AC15 (behaviour half): text leaves push down as table operators with raw values on pg, mysql, sqlserver, bigquery; too-short `contains`-family operands and all regex leaves stay residual.
- [ ] Residual evaluation matches pushdown semantics (plain = case-sensitive, `i*` = case-insensitive).
- [ ] Provider capability sets: mysql/sqlserver = BASE + text_match; bigquery = sql + text_match; pg unchanged.
- [ ] `docs/QSURL.md` documents the new tokens and the migration; `tests/qsurl/test_docs.py` passes.
- [ ] `ruff check querysource/qsurl/translate.py querysource/qsurl/residual.py` is clean.

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/qsurl/test_translate.py -q`
- `pytest tests/qsurl/test_residual.py -q`
- `pytest tests/qsurl/test_qs_residual.py -q`
- `pytest tests/qsurl/test_provider_capabilities.py -q`
- `pytest tests/qsurl/test_docs.py -q`
- `pytest tests/e2e/test_qsurl_dry_run.py -q`

---

## Test Specification

See the FILL IN checklist: every listed expectation change is mandatory.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug filter-with-partial-matching --feature-id FEAT-180`)
2. **Read the spec** at the path listed above for full context (§2 rendered-forms table is normative)
3. **Check dependencies** — every `Depends-on` task must be `"done"` in the
   per-spec index `sdd/tasks/index/filter-with-partial-matching.json`
4. **Verify the Codebase Contract** — before writing ANY code:
   - Confirm every import in "Verified Imports" still exists (`grep` or `read` the source)
   - Re-run `grep -c` for every MODIFY anchor in the blueprint; a count that differs from the
     one recorded means the anchor moved — re-locate it; a count of `0` means STOP and report drift
   - **NEVER** reference an import, attribute, or method not in the contract without verifying it exists
5. **Update status** in `sdd/tasks/index/filter-with-partial-matching.json` → `"in-progress"`
   (set `started_at`) and commit only that index file
6. **Implement** — start from the Implementation Blueprint blocks, complete every
   `# FILL IN:` marker, and never change a signature or path the blueprint fixes
7. **Verify** all acceptance criteria are met — run the Validation Commands
8. **Commit the code** — stage only the files this task lists (never `git add .` / `-A`)
9. **Close the task** with `scripts/sdd/close_task.sh TASK-853 filter-with-partial-matching verified`
   — it moves this file to `sdd/tasks/completed/` and marks it `"done"` in the
   index; never move or copy the file by hand
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
