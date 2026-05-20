# TASK-662: Component Docstrings

**Feature**: FEAT-095 — MultiQuery Documentation System
**Spec**: `sdd/specs/multiquery-documentation.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-661
**Assigned-to**: unassigned

---

## Context

> Implements Module 3 from the spec. Adds comprehensive docstrings to all 7
> operators and 8 transforms so that `get_description()` and `get_schema()`
> return meaningful documentation. Without docstrings, the introspection
> classmethods produce empty descriptions.

---

## Scope

- Add Google-style docstrings to all 7 operators:
  - `Join`, `Concat`, `Melt`, `Merge`, `GroupBy`, `Info`, `Filter`
- Add Google-style docstrings to all 8 transforms:
  - `tPandas`, `tOrder`, `Map`, `correlation`, `crosstab`, `pivot`, `Forecast`, `GoogleMaps`
- Each docstring must include:
  - **First line**: short description
  - **Usage**: how this component is used in a MultiQuery pipeline
  - **Attributes**: list of configurable attributes with types and defaults
  - **Example**: JSON pipeline snippet showing the component in use
- Preserve existing docstrings where they exist (enhance, don't replace)
- Do NOT modify class logic, signatures, or imports

**NOT in scope**: Base class changes (TASK-660/661), registry (TASK-663), any functional code changes

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/operators/Join.py` | MODIFY | Add docstring |
| `querysource/queries/multi/operators/Concat.py` | MODIFY | Enhance docstring |
| `querysource/queries/multi/operators/Melt.py` | MODIFY | Add docstring |
| `querysource/queries/multi/operators/Merge.py` | MODIFY | Enhance docstring |
| `querysource/queries/multi/operators/GroupBy.py` | MODIFY | Enhance docstring |
| `querysource/queries/multi/operators/Info.py` | MODIFY | Enhance docstring |
| `querysource/queries/multi/operators/filter/flt.py` | MODIFY | Add docstring |
| `querysource/queries/multi/transformations/tPandas.py` | MODIFY | Enhance docstring |
| `querysource/queries/multi/transformations/tOrder.py` | MODIFY | Enhance docstring |
| `querysource/queries/multi/transformations/Map.py` | MODIFY | Enhance docstring |
| `querysource/queries/multi/transformations/correlation.py` | MODIFY | Add docstring |
| `querysource/queries/multi/transformations/crosstab.py` | MODIFY | Add docstring |
| `querysource/queries/multi/transformations/pivot.py` | MODIFY | Add docstring |
| `querysource/queries/multi/transformations/Forecast.py` | MODIFY | Add docstring |
| `querysource/queries/multi/transformations/google/maps.py` | MODIFY | Add docstring |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
No new imports needed — this task only adds docstrings.

### Existing Signatures to Use
```python
# Operators — add docstring to these classes (do NOT change signatures)
class Join(AbstractOperator):  # operators/Join.py:13
    # attrs: _type (str, default 'inner'), _left, _right, _join_conditions
class Concat(AbstractOperator):  # operators/Concat.py:8
    # existing docstring: "Concat to Dataframes in one."
class Melt(AbstractOperator):  # operators/Melt.py:9
    # attrs: _id_vars, _na_cols
class Merge(AbstractOperator):  # operators/Merge.py:9
    # attrs: _on, _how (default 'inner'), _left_on, _right_on, _suffixes
    # existing docstring: "Merge two DataFrames with support for various join types..."
class GroupBy(AbstractOperator):  # operators/GroupBy.py:32
    # attrs: _columns (dict), _by (list), _nan_by_with (str)
    # existing docstring: extensive table of aggregation functions
class Info(AbstractOperator):  # operators/Info.py:10
    # existing docstring: "Extract and return detailed information..."
class Filter(AbstractOperator):  # operators/filter/flt.py:12
    # attrs: conditions, fields (dict), _filter (list), _operator (str, default '&')

# Transforms — add docstring to these classes
class tPandas(AbstractTransform):  # transformations/tPandas.py:12
    # attrs: type (str), condition (str), pd_args (dict)
    # existing docstring: "tPandas … abstract interface for performing various data transformations..."
class tOrder(tPandas):  # transformations/tOrder.py:9
    # attrs: _column (Union[str, list]), _ascending (Union[bool, list]), _na_position (str)
    # existing docstring: "tOrder … designed to order a Pandas DataFrame..."
class Map(AbstractTransform):  # transformations/Map.py:14
    # attrs: replace_columns (bool), reset_index (bool)
    # existing docstring: "Map Transform: changing the shape of the data."
class correlation(AbstractTransform):  # transformations/correlation.py:41
    # attrs: reset_index (bool), numeric_only (bool), method (default 'pearson')
class crosstab(AbstractTransform):  # transformations/crosstab.py:10
    # attrs: reset_index (bool), _type (default 'crosstab')
class pivot(AbstractTransform):  # transformations/pivot.py:10
    # attrs: reset_index (bool), _type, _multilevel (bool), _pd_args (dict), _fill_value
class Forecast(AbstractTransform):  # transformations/Forecast.py:14
    # attrs: reset_index (bool), _order (tuple, default (1,1,1)), _steps (int), _freq (str), model_args (dict)
class GoogleMaps(AbstractTransform):  # transformations/google/maps.py:11
    # attrs: zoom (int), map_scale (int), timestamp_key (str), departure_time (str)
```

### Does NOT Exist
- ~~`Filter.__init__` docstring`~~ — Filter has no existing docstring
- ~~`correlation.__init__` docstring~~ — correlation has no existing docstring

---

## Implementation Notes

### Docstring Format

Follow Google-style format. Example for `Join`:
```python
class Join(AbstractOperator):
    """Join two or more DataFrames on shared columns or index.

    Performs SQL-style joins (inner, left, right, outer) between DataFrames
    in the pipeline data dictionary.

    Attributes:
        type: Join type — 'inner', 'left', 'right', or 'outer'. Default: 'inner'.
        left: Name of the left DataFrame key in the data dictionary.
        right: Name of the right DataFrame key in the data dictionary.
        join_conditions: List of join condition dicts specifying column mappings.
        on: Column name(s) to join on (alias for 'using').

    Example:
        {
            "Join": {
                "type": "inner",
                "left": "revenue",
                "right": "costs",
                "on": "date"
            }
        }
    """
```

### Key Constraints
- Only modify docstrings — no code changes
- Preserve existing docstring content where present (enhance with attributes/example)
- Use attribute names as they appear in kwargs (public names), not the internal `_` prefixed names
- Each example should show a realistic MultiQuery pipeline step in JSON format

---

## Acceptance Criteria

- [ ] All 7 operators have docstrings with: description, attributes, example
- [ ] All 8 transforms have docstrings with: description, attributes, example
- [ ] `get_description()` returns meaningful content for each class
- [ ] No functional code changes — only docstrings modified
- [ ] No import errors: `source .venv/bin/activate && python -c "from querysource.queries.multi.operators.Join import Join; print(Join.__doc__[:50])"`

---

## Test Specification

No dedicated test file needed — verification is via import + docstring check:
```bash
source .venv/bin/activate
python -c "
from querysource.queries.multi.operators.Join import Join
from querysource.queries.multi.operators.Concat import Concat
from querysource.queries.multi.transformations.Map import Map
from querysource.queries.multi.transformations.correlation import correlation
for cls in [Join, Concat, Map, correlation]:
    doc = cls.__doc__
    assert doc, f'{cls.__name__} missing docstring'
    assert 'Attributes:' in doc or 'Example:' in doc, f'{cls.__name__} docstring incomplete'
    print(f'{cls.__name__}: OK ({len(doc)} chars)')
"
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-documentation.spec.md` for full context
2. **Check dependencies** — verify TASK-661 is in `sdd/tasks/completed/`
3. **Read each file** before modifying to understand existing docstrings
4. **Add/enhance docstrings** following the Google-style format
5. **Verify** no code changes were made accidentally
6. **Move this file** to `sdd/tasks/completed/TASK-662-component-docstrings.md`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
