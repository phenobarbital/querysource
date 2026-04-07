# Feature Specification: GenData Component

**Feature ID**: FEAT-005
**Date**: 2026-03-20
**Author**: Jesus Lara
**Status**: approved
**Target version**: 1.x

---

## 1. Motivation & Business Requirements

### Problem Statement

FlowTask has components for loading data from files (`OpenWithPandas`), databases (`QueryToPandas`), and iterating date ranges (`DateList`), but lacks a component that **generates an artificial DataFrame from rules**. Users frequently need synthetic date-based DataFrames — e.g., a column of every Monday between two dates — to serve as scaffolding for joins, reports, or scheduling pipelines. Today this requires custom Python or an external step before the pipeline runs.

### Goals
- Provide a declarative YAML-configured component (`GenData`) that produces a pandas DataFrame of generated rows based on rules.
- Support date-sequence generation with configurable interval (in days) and optional day-of-week alignment.
- Output a standard pandas DataFrame that integrates seamlessly with existing `t*` transformation components.
- Extensible rule system so future generators (numeric ranges, categorical cycles, etc.) can be added.

### Non-Goals (explicitly out of scope)
- Full synthetic data generation (Faker-style random names, addresses, etc.).
- Statistical distribution-based data generation.
- Multi-column rule composition in v1 (each rule produces one column; cross-column dependencies are deferred).

---

## 2. Architectural Design

### Overview

`GenData` is a new `FlowComponent` that reads a list of **column rules** from its YAML configuration, evaluates each rule to produce a column (Series), and assembles them into a single DataFrame. The first version focuses on a `date_sequence` rule type.

### Component Diagram
```
YAML Config
    │
    ▼
GenData (FlowComponent)
    │
    ├── RuleRegistry  ─── DateSequenceRule
    │                  ─── (future rules)
    │
    ▼
pd.DataFrame  ──→  downstream t* components
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `FlowComponent` | extends | GenData inherits from FlowComponent |
| `PandasDataframe` | uses | Uses the mixin for DataFrame creation |
| `DateList` | reference | Similar date logic; GenData produces a DataFrame instead of iterating |
| `t*` components | downstream | Output DataFrame feeds into tJoin, tFilter, etc. |

### Data Models
```python
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date

class DateSequenceRule(BaseModel):
    """Rule for generating a sequence of dates."""
    type: str = Field("date_sequence", description="Rule type identifier")
    column_name: str = Field(..., description="Name of the output column")
    firstdate: date = Field(..., description="Start of date range (inclusive)")
    lastdate: date = Field(..., description="End of date range (inclusive)")
    interval: int = Field(default=1, ge=1, description="Step size in days")
    dow: Optional[int] = Field(
        default=None,
        ge=0,
        le=6,
        description="Day of week to align to (0=Monday, 6=Sunday). "
                    "If set, sequence starts on the first matching day >= firstdate."
    )
```

### New Public Interfaces
```python
class GenData(FlowComponent):
    """Generate an artificial DataFrame from declarative rules.

    YAML example:
        GenData:
          rules:
            - type: date_sequence
              column_name: weekly_date
              firstdate: "2024-01-01"
              lastdate: "2026-03-20"
              interval: 7
              dow: 0
    """
    async def start(self, **kwargs) -> bool:
        """Parse and validate rules from YAML config."""
        ...

    async def run(self) -> bool:
        """Execute all rules and assemble the output DataFrame."""
        ...
```

### YAML Configuration Example
```yaml
GenData:
  rules:
    - type: date_sequence
      column_name: weekly_date
      firstdate: "{firstdate}"
      lastdate: "{lastdate}"
      interval: 7
      dow: 0
    - type: date_sequence
      column_name: monthly_first
      firstdate: "2024-01-01"
      lastdate: "2026-03-20"
      interval: 30
```

---

## 3. Module Breakdown

### Module 1: GenData Component
- **Path**: `flowtask/components/GenData.py`
- **Responsibility**: FlowComponent subclass that reads `rules` from config, delegates to rule handlers, and assembles the output DataFrame.
- **Depends on**: FlowComponent, Module 2 (rule handlers)

### Module 2: Date Sequence Rule Handler
- **Path**: `flowtask/components/gendata/__init__.py` + `flowtask/components/gendata/date_sequence.py`
- **Responsibility**: Implements the `date_sequence` rule type. Generates a list of `datetime.date` values from `firstdate` to `lastdate` with `interval` and optional `dow` alignment.
- **Depends on**: pandas, datetime, Pydantic model

### Module 3: Unit Tests
- **Path**: `tests/test_gendata.py`
- **Responsibility**: Tests for GenData component and date_sequence rule.
- **Depends on**: Module 1, Module 2

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_date_sequence_basic` | Module 2 | Daily sequence from 2024-01-01 to 2024-01-10 produces 10 rows |
| `test_date_sequence_interval` | Module 2 | interval=7 produces weekly dates |
| `test_date_sequence_dow_alignment` | Module 2 | dow=0 shifts start to first Monday >= firstdate |
| `test_date_sequence_dow_on_correct_day` | Module 2 | If firstdate is already the target dow, no shift occurs |
| `test_date_sequence_dow_all_days` | Module 2 | dow=0..6 each produce correct weekday alignment |
| `test_date_sequence_single_day` | Module 2 | firstdate == lastdate with matching dow produces 1 row |
| `test_date_sequence_no_match` | Module 2 | Range too short for dow alignment produces empty DataFrame |
| `test_gendata_multiple_rules` | Module 1 | Two rules produce a DataFrame with two columns |
| `test_gendata_variable_substitution` | Module 1 | `{firstdate}` in config resolves from pipeline variables |
| `test_gendata_invalid_rule_type` | Module 1 | Unknown rule type raises ComponentError |

### Integration Tests
| Test | Description |
|---|---|
| `test_gendata_to_tfilter` | GenData output piped to tFilter validates downstream compatibility |

### Test Data / Fixtures
```python
@pytest.fixture
def date_sequence_config():
    return {
        "rules": [
            {
                "type": "date_sequence",
                "column_name": "weekly_date",
                "firstdate": "2024-01-01",
                "lastdate": "2024-03-31",
                "interval": 7,
                "dow": 0,
            }
        ]
    }
```

---

## 5. Acceptance Criteria

- [ ] `GenData` component is loadable via `getComponent("GenData")`
- [ ] `date_sequence` rule produces correct dates for basic interval (no dow)
- [ ] `date_sequence` rule with `dow` aligns to the correct weekday
- [ ] Multiple rules in a single GenData produce a multi-column DataFrame
- [ ] Pipeline variables (`{firstdate}`, `{lastdate}`) are resolved in rule values
- [ ] All unit tests pass (`pytest tests/test_gendata.py -v`)
- [ ] No breaking changes to existing components
- [ ] Component follows FlowComponent conventions (logging, `_result`, metrics)

---

## 6. Implementation Notes & Constraints

### Patterns to Follow
- Inherit from `FlowComponent` (like `DateList`, `QueryToPandas`)
- Use `self._result` to store the output DataFrame
- Use `self._logger` for all logging
- Resolve pipeline variables via `self._variables` / `self.getVar()`
- Parse dates using `datetime.datetime.strptime` with configurable format (default `%Y-%m-%d`)
- Use Pydantic for rule validation

### Day-of-Week Alignment Algorithm
```python
# dow: 0=Monday (matching Python's date.weekday())
def align_to_dow(start: date, dow: int) -> date:
    days_ahead = dow - start.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return start + timedelta(days=days_ahead)
```

### Known Risks / Gotchas
- Large date ranges with interval=1 can produce very large DataFrames. Consider adding an optional `max_rows` safety limit.
- Variable substitution (`{firstdate}`) must handle both string dates and `datetime` objects from upstream components.
- `dow` uses Python convention (0=Monday), which differs from some systems (0=Sunday). Document clearly.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `pandas` | `>=1.5` | DataFrame creation (already a project dependency) |
| `pydantic` | `>=2.0` | Rule validation (already a project dependency) |

---

## 7. Open Questions

- [ ] Should GenData support a `date_format` parameter per rule for output formatting, or always produce `datetime.date` objects? — *Owner: Jesus Lara*: provide a date_format.
- [ ] Should multiple rules with different row counts be joined (cross-join) or aligned (with NaN padding)? — *Owner: Jesus Lara*: provide a cross join.
- [ ] Should we support reading rules from an external YAML file in addition to inline config? — *Owner: Jesus Lara*: inline config is ok for now.

---

## Worktree Strategy

- **Isolation unit**: `per-spec` (sequential tasks)
- All three modules are tightly coupled and should be implemented in a single worktree.
- **Cross-feature dependencies**: None — this is a standalone new component.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-03-20 | Jesus Lara | Initial draft |
