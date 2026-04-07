# Feature Specification: Excel to Postgres Flowtask Pipeline

**Feature ID**: FEAT-001
**Date**: 2026-03-04
**Author**: Antigravity
**Status**: review
**Target version**: 1.0.0

---

## 1. Motivation & Business Requirements

> Why does this feature exist? What problem does it solve?

### Problem Statement
The user needs to create a Flowtask task definition in YAML syntax that facilitates the ingestion of data from an Excel file, applies row-level transformations to the dataset, and outputs the final processed data to a PostgreSQL database table.

### Goals
- Define a complete Flowtask pipeline in YAML.
- Integrate the `OpenWithPandas` component to accurately read Excel data.
- Utilize the `TransformRows` component to manipulate specific dataset fields.
- Leverage the `TableOutput` component to insert the final dataframe into PostgreSQL.

### Non-Goals (explicitly out of scope)
- Implementation of new custom components in Python.
- Database provisioning or environment setup.

---

## 2. Architectural Design

### Overview
This flow relies on three sequential Flowtask components. The pipeline receives or targets an Excel file, processes it into a Pandas DataFrame using `OpenWithPandas`, transforms data using `TransformRows` (e.g., standardizing text or computing new fields based on existing data), and writes it to a Postgres schema using `TableOutput`.

### Component Diagram
```
[Excel File / Input] ──→ OpenWithPandas ──→ TransformRows ──→ TableOutput ──→ [PostgreSQL DB]
```

### Data Models / Flowtask Definition
The primary artifact for this feature is the following YAML pipeline configuration:

```yaml
name: Excel to PostgreSQL Load
description: Pipeline to open an Excel file, perform row transformations, and load into a PostgreSQL table.
steps:
  - OpenWithPandas:
      # Required arguments based on the JSON schema
      file_engine: openpyxl
      model: ExcelDataModel
      tablename: target_table
      use_map: "false"
      map: "default_map"
  
  - TransformRows:
      replace_columns: true
      fields:
        # Example transformation mapping
        processed_date:
          value:
            - convert_timezone
            - from_tz: UTC
        standardized_name:
          value:
            - uppercase
            - column: original_name

  - TableOutput:
      flavor: postgres
      schema: public
      tablename: target_table
      if_exists: append
      pk:
        - id
```

---

## 3. Module Breakdown

### Module 1: Flowtask YAML configuration
- **Path**: `tasks/programs/excel_ingestion/tasks/excel_to_postgres.yaml` (Example Path)
- **Responsibility**: Houses the logic coordinating the three Flowtask components.
- **Depends on**: Flowtask core components (`OpenWithPandas`, `TransformRows`, `TableOutput`).

---

## 4. Test Specification

### Integration Tests
| Test | Description |
|---|---|
| `test_excel_to_postgres_pipeline` | Full pipeline execution reading a sample `.xlsx` file and validating Postgres row inserts. |

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] A valid flowtask YAML configuration is created using `OpenWithPandas`, `TransformRows`, and `TableOutput`.
- [ ] Required parameters for all components conform to their respective `schema.json` and `doc.json`.
- [ ] Documentation updated in `docs/` if this serves as a new example.

---

## 6. Implementation Notes & Constraints

### Patterns to Follow
- Follow standard Flowtask YAML list-of-dicts component declaration.
- Provide explicit required attributes for `OpenWithPandas` (model, map, tablename, use_map, file_engine).
- Make sure `TableOutput` flavor is properly set to `postgres`.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `openpyxl` | `>=3.0` | Required file engine by `OpenWithPandas` for Excel extraction. |
| `pandas` | `>=1.0` | Required for dataframe operations. |

---

## 7. Open Questions

- [ ] Where should the target YAML file specifically be saved within the `tasks/` directory? — *Owner: User*
- [ ] Are there specific row transformations (e.g., data cleansing on specific fields) required for `TransformRows`? — *Owner: User*

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-03-04 | Antigravity | Initial draft with YAML structure |
