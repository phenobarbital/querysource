# TASK-010: GenData FlowComponent

**Feature**: GenData Component
**Spec**: `sdd/specs/gendata-component.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-009
**Assigned-to**: unassigned

---

## Context

This task implements the main `GenData` FlowComponent (Module 1 from the spec). It reads a `rules` list from YAML configuration, delegates each rule to the appropriate handler (from TASK-009), and assembles the results into a single pandas DataFrame using cross-joins for rules with different row counts.

---

## Scope

- Create `flowtask/components/GenData.py` inheriting from `FlowComponent`.
- Implement `start()`: parse and validate the `rules` list from YAML config, resolve pipeline variables (`{firstdate}`, etc.) in rule values.
- Implement `run()`: execute each rule, collect the resulting Series, and assemble into a DataFrame.
- For multiple rules with different row counts, use cross-join (per open question resolution).
- Implement a rule registry/dispatcher that maps `type` strings to handler functions.
- Add logging and metrics following FlowComponent conventions.
- Write unit tests for the component in `tests/test_gendata_component.py`.

**NOT in scope**: The rule handler logic itself (TASK-009), integration tests with downstream components.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `flowtask/components/GenData.py` | CREATE | Main GenData FlowComponent |
| `flowtask/components/gendata/__init__.py` | MODIFY | Add rule registry with dispatcher |
| `tests/test_gendata_component.py` | CREATE | Unit tests for GenData component |

---

## Implementation Notes

### Pattern to Follow

Reference `flowtask/components/DateList.py` for the FlowComponent lifecycle:

```python
import asyncio
from collections.abc import Callable
import pandas as pd
from ..exceptions import ComponentError
from .flow import FlowComponent
from .gendata import get_rule_handler


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
    _version = "1.0.0"

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop = None,
        job: Callable = None,
        stat: Callable = None,
        **kwargs,
    ):
        self._rules_config = []
        self._parsed_rules = []
        super().__init__(loop=loop, job=job, stat=stat, **kwargs)

    async def start(self, **kwargs) -> bool:
        """Parse and validate rules from YAML config."""
        await super().start(**kwargs)
        if not hasattr(self, "rules") or not self.rules:
            raise ComponentError("GenData: 'rules' attribute is required")
        # Resolve pipeline variables in rule values
        self._rules_config = self._resolve_variables(self.rules)
        # Validate each rule
        for rule_config in self._rules_config:
            rule_type = rule_config.get("type")
            handler = get_rule_handler(rule_type)
            if handler is None:
                raise ComponentError(f"GenData: unknown rule type '{rule_type}'")
        self.add_metric("RULES_COUNT", len(self._rules_config))
        return True

    async def run(self) -> bool:
        """Execute all rules and assemble the output DataFrame."""
        columns = {}
        for rule_config in self._rules_config:
            handler = get_rule_handler(rule_config["type"])
            series = handler(rule_config)
            columns[series.name] = series
        # Cross-join if different lengths
        self._result = self._assemble_dataframe(columns)
        self.add_metric("OUTPUT_ROWS", len(self._result))
        return True
```

### Cross-Join Assembly
```python
def _assemble_dataframe(self, columns: dict[str, pd.Series]) -> pd.DataFrame:
    """Assemble columns into a DataFrame, cross-joining if lengths differ."""
    if not columns:
        return pd.DataFrame()
    series_list = list(columns.values())
    if len(series_list) == 1:
        return series_list[0].to_frame()
    # Cross-join: iteratively merge
    result = series_list[0].to_frame()
    result["_key"] = 1
    for s in series_list[1:]:
        right = s.to_frame()
        right["_key"] = 1
        result = result.merge(right, on="_key")
    result = result.drop("_key", axis=1)
    return result.reset_index(drop=True)
```

### Key Constraints
- Pipeline variables (`{firstdate}`, `{lastdate}`) must be resolved before rule validation.
- Use `self._variables` and `self.getVar()` to resolve variables (follow DateList pattern).
- Store result in `self._result` for downstream components.
- Log rule count and output row count as metrics.

### References in Codebase
- `flowtask/components/DateList.py` — FlowComponent lifecycle pattern
- `flowtask/components/flow.py` — FlowComponent base class
- `flowtask/components/gendata/date_sequence.py` — rule handler (TASK-009)
- `flowtask/components/__init__.py` — component loading via `getComponent()`

---

## Acceptance Criteria

- [ ] `GenData` is loadable via `getComponent("GenData")`
- [ ] Single rule produces a single-column DataFrame
- [ ] Multiple rules produce a cross-joined multi-column DataFrame
- [ ] Pipeline variables are resolved in rule values
- [ ] Unknown rule type raises `ComponentError`
- [ ] Output stored in `self._result`
- [ ] Metrics logged (rule count, output rows)
- [ ] All tests pass: `pytest tests/test_gendata_component.py -v`

---

## Test Specification

```python
# tests/test_gendata_component.py
import pytest
from datetime import date
import pandas as pd


class TestGenDataComponent:
    def test_single_rule_produces_dataframe(self):
        """Single date_sequence rule produces a DataFrame with one column."""
        # Setup GenData with rules config, call start() + run()
        # Assert self._result is a DataFrame with expected column

    def test_multiple_rules_cross_join(self):
        """Two rules with different row counts produce a cross-joined DataFrame."""
        # Rule 1: 4 dates, Rule 2: 3 dates -> 12 rows

    def test_variable_substitution(self):
        """Pipeline variables like {firstdate} are resolved in rule values."""

    def test_unknown_rule_type_raises(self):
        """Unknown rule type raises ComponentError during start()."""

    def test_empty_rules_raises(self):
        """Missing or empty rules raises ComponentError."""

    def test_metrics_logged(self):
        """Metrics include RULES_COUNT and OUTPUT_ROWS."""
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/gendata-component.spec.md` for full context
2. **Check dependencies** — verify TASK-009 is in `sdd/tasks/completed/`
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"`
4. **Implement** following the scope and notes above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-010-gendata-component.md`
7. **Update index** → `"done"`
8. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
