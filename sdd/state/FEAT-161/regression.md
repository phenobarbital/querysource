# FEAT-161 regression report (pandas 3.0.6)

No valid pandas 2.2 baseline exists (see baseline.txt). Comparison: full `tests/` run on the
feature worktree vs the `dev` tip, both on the pandas 3.0.6 venv, with
`-W error::pandas.errors.ChainedAssignmentError --continue-on-collection-errors`.
- dev tip + pandas 3: 169 failed, 2657 passed, 2 collection errors
- feature branch (before tExplode/statsmodels fixes): 168 failed, 2624 passed, 2 collection errors

| Failure | Cause | Disposition |
|---|---|---|
| tests/test_multiqs_column_transforms.py::TestFilterCols::test_filter_cols_all_empty | str dtype (not object) | fixed by TASK-837 |
| tests/test_texplode.py (3, advanced mode, list-of-str items) | json_normalize now raises on non-dict items (other) | fixed in tExplode._explode_advanced (only normalise dicts) |
| tests/test_catalog_forecast.py collection error (statsmodels `deprecate_kwarg() missing new_arg_name`) | statsmodels==0.14.2 incompatible with pandas 3 (other) | pyproject pin raised to statsmodels>=0.14.6 (lock: 0.15.0); needs `uv sync` of the shared venv to take effect |
| `cannot import name 'QuerySource' from 'querysource'` collection error | not pandas; present on dev | out of scope |
| tests/test_sql_guard.py (87), tests/test_pgsql_jsonb_filters.py (32), airtable (9), test_remote_executor (5), e2e dry_run (5), tenants (4), `_FakeThread ... 'definition'` (unit/integration multiqs output tests) and others | SQL parser/guard, Airtable, executors, test-double signature; no pandas involvement | present on dev tip with identical results; out of scope |
