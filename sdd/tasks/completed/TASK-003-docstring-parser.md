# TASK-003: Docstring Parser

**Feature**: Component Documentation API
**Spec**: `sdd/specs/component-documentation-api.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: claude-session-2026-03-03

---

## Context

This task implements the foundation for extracting documentation from Flowtask component classes. It parses the docstring table format (`:widths: auto` with `| attribute | Required | Description |`) used in component classes like `DownloadFromBase`, `AddDataset`, etc.

Reference: Spec Section 3 - Module 1: Docstring Parser

---

## Scope

- Implement `DocstringParser` class to parse component docstrings
- Extract component description (text before the table)
- Parse the attribute table format with columns: name, required (Yes/No), description
- Extract YAML/JSON example blocks from docstrings
- Handle edge cases: empty docstrings, malformed tables, missing examples

**NOT in scope**:
- JSON Schema generation (TASK-004)
- File scanning/discovery (TASK-005)
- Writing output files (TASK-005)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `flowtask/documentation/__init__.py` | CREATE | Package init, export public classes |
| `flowtask/documentation/parser.py` | CREATE | DocstringParser implementation |
| `flowtask/documentation/models.py` | CREATE | Pydantic models (ComponentDoc, ComponentAttribute) |
| `tests/unit/documentation/__init__.py` | CREATE | Test package init |
| `tests/unit/documentation/test_parser.py` | CREATE | Unit tests for parser |

---

## Implementation Notes

### Pattern to Follow
```python
# flowtask/documentation/parser.py
import re
from typing import List, Optional
from .models import ComponentDoc, ComponentAttribute

class DocstringParser:
    """Parse component docstrings into structured format."""

    # Regex patterns for parsing
    TABLE_PATTERN = re.compile(r'\|\s*(\w+)\s*\|\s*(Yes|No)\s*\|\s*(.+?)\s*\|')
    EXAMPLE_PATTERN = re.compile(r'```(?:yaml|json)\s*(.*?)```', re.DOTALL)

    def parse(self, docstring: str) -> Optional[ComponentDoc]:
        """Parse a docstring into ComponentDoc."""
        if not docstring:
            return None
        ...

    def _extract_description(self, docstring: str) -> str:
        """Extract description before :widths: auto marker."""
        ...

    def _parse_attributes(self, docstring: str) -> List[ComponentAttribute]:
        """Parse the attribute table."""
        ...

    def _extract_examples(self, docstring: str) -> List[str]:
        """Extract code blocks marked as yaml or json."""
        ...
```

### Key Constraints
- Must handle the `:widths: auto` marker as table start indicator
- Attribute table uses `|` delimiters with format: `| name | Yes/No | description |`
- Some components have malformed tables (extra columns, missing data)
- Examples are in fenced code blocks with `yaml` or `json` language markers
- Must be lenient - don't fail on partially documented components

### References in Codebase
- `flowtask/components/DownloadFrom.py` — example docstring format (lines 20-75)
- `flowtask/components/OpenWithBase.py` — another example

---

## Acceptance Criteria

- [ ] `DocstringParser.parse()` returns `ComponentDoc` for valid docstrings
- [ ] Attribute table with `:widths: auto` format is parsed correctly
- [ ] Examples in ```yaml blocks are extracted with preserved formatting
- [ ] Empty/malformed docstrings return `None` without raising exceptions
- [ ] All tests pass: `pytest tests/unit/documentation/test_parser.py -v`
- [ ] No linting errors: `ruff check flowtask/documentation/`

---

## Test Specification

```python
# tests/unit/documentation/test_parser.py
import pytest
from flowtask.documentation.parser import DocstringParser
from flowtask.documentation.models import ComponentDoc


@pytest.fixture
def parser():
    return DocstringParser()


@pytest.fixture
def sample_docstring():
    return '''
    DownloadFromBase

    Abstract base class for downloading files from various sources.

    :widths: auto

    | credentials | Yes | Dictionary containing username and password |
    | overwrite   | No  | Boolean flag to overwrite existing files    |
    | ssl         | No  | Boolean flag for SSL connection             |

    Example:

    ```yaml
    DownloadFrom:
      credentials:
        username: user
        password: pass
      overwrite: true
    ```
    '''


class TestDocstringParser:
    def test_parse_returns_component_doc(self, parser, sample_docstring):
        """Parser returns ComponentDoc for valid docstring."""
        result = parser.parse(sample_docstring)
        assert isinstance(result, ComponentDoc)
        assert result.name == "DownloadFromBase"

    def test_parse_extracts_attributes(self, parser, sample_docstring):
        """Parser extracts all attributes from table."""
        result = parser.parse(sample_docstring)
        assert len(result.attributes) == 3
        assert result.attributes[0].name == "credentials"
        assert result.attributes[0].required is True
        assert result.attributes[1].required is False

    def test_parse_extracts_examples(self, parser, sample_docstring):
        """Parser extracts YAML examples."""
        result = parser.parse(sample_docstring)
        assert len(result.examples) == 1
        assert "DownloadFrom:" in result.examples[0]

    def test_parse_empty_docstring(self, parser):
        """Parser returns None for empty docstring."""
        assert parser.parse("") is None
        assert parser.parse(None) is None

    def test_parse_no_table(self, parser):
        """Parser handles docstring without attribute table."""
        docstring = "Simple component description without table."
        result = parser.parse(docstring)
        assert result is None or result.attributes == []

    def test_parse_extracts_description(self, parser, sample_docstring):
        """Parser extracts description before table."""
        result = parser.parse(sample_docstring)
        assert "Abstract base class" in result.description
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/component-documentation-api.spec.md` for full context
2. **Check dependencies** — this task has no dependencies
3. **Update status** in `sdd/tasks/.index.json` → `"in-progress"` with your session ID
4. **Implement** following the scope and notes above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-003-docstring-parser.md`
7. **Update index** → `"done"`
8. **Fill in the Completion Note** below

---

## Completion Note

**Completed by**: claude-session-2026-03-03
**Date**: 2026-03-03
**Notes**:
- Created `flowtask/documentation/` package with models.py, parser.py, and __init__.py
- Implemented DocstringParser with robust handling for:
  - Multi-line attribute descriptions (continuation rows)
  - Variable spacing in table cells
  - Duplicate attribute detection and deduplication
  - Multiple example blocks (yaml and json)
  - Real-world docstring formats from existing components
- Added 18 comprehensive unit tests covering all edge cases
- Verified parser works with real component (DownloadFromBase: 18 attributes parsed)
- All tests pass, no linting errors

**Deviations from spec**:
- Added `_extract_name()` method to automatically extract component name from first line
- Enhanced continuation row handling beyond the basic pattern in spec
- Added duplicate attribute detection to handle real-world docstrings with repeated entries
