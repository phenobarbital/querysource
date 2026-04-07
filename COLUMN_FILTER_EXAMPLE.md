# Column-to-Column Filter Reference

## Overview

The Filter component now supports comparing values between two columns using the `{"$column": "column_name"}` syntax. This allows you to filter rows based on relationships between columns without creating intermediate computed columns.

## Syntax

```json
{
  "filter": [
    {
      "column": "column_a",
      "expression": ">=",
      "value": {"$column": "column_b"}
    }
  ]
}
```

### Parameters

- **column**: The primary column to evaluate
- **expression**: The comparison operator (see below)
- **value**: Either:
  - A scalar value (string, number, date)
  - A column reference: `{"$column": "other_column_name"}`

## Supported Operators

All standard comparison operators work with column-to-column filters:

| Operator | Description | Example |
|----------|-------------|---------|
| `>` | Greater than | `fecha_escuchado > fecha_podcast` |
| `>=` | Greater than or equal | `fecha_escuchado >= fecha_podcast` |
| `<` | Less than | `edad < edad_maxima` |
| `<=` | Less than or equal | `saldo <= limite` |
| `==` | Equal | `categoria == categoria_esperada` |
| `!=` | Not equal | `status != status_anterior` |

String operators also work with columns:
- `contains`, `not_contains`
- `startswith`, `not_startswith`
- `endswith`, `not_endswith`
- `regex`, `not_regex`

## Use Case: Calls + Podcasts

Given two joined DataFrames:
- **Calls**: Contains `id_llamada`, `fecha_llamada`, `usuario_id`, etc.
- **Podcasts**: Contains `id_podcast`, `fecha_creacion`, `usuario_id`, `fecha_escuchado`, etc.

**Problem**: Find rows where the podcast was listened to **after** it was created.

### Without Column Filter (old way)
```python
# Create an intermediate column
df['can_listen'] = df['fecha_escuchado'] >= df['fecha_creacion']

# Then filter
{
  "filter": [
    {"column": "can_listen", "expression": "==", "value": True}
  ]
}
```

### With Column Filter (new way)
```json
{
  "queries": {
    "calls": { "slug": "get-calls" },
    "podcasts": { "slug": "get-podcasts" }
  },
  "Join": {
    "left": "calls",
    "right": "podcasts",
    "on": ["usuario_id"]
  },
  "Filter": {
    "filter": [
      {
        "column": "fecha_escuchado",
        "expression": ">=",
        "value": {"$column": "fecha_creacion"}
      }
    ]
  }
}
```

## Multiple Column Filters

You can combine multiple column-to-column filters with AND logic:

```json
{
  "Filter": {
    "filter": [
      {
        "column": "fecha_escuchado",
        "expression": ">=",
        "value": {"$column": "fecha_creacion"}
      },
      {
        "column": "duracion_escuchado",
        "expression": "<",
        "value": {"$column": "duracion_total"}
      }
    ]
  }
}
```

This filters to rows where:
- `fecha_escuchado >= fecha_creacion` **AND**
- `duracion_escuchado < duracion_total`

## Mixed Scalar and Column Filters

You can mix column-to-column filters with regular scalar filters:

```json
{
  "Filter": {
    "filter": [
      {
        "column": "fecha_escuchado",
        "expression": ">=",
        "value": {"$column": "fecha_creacion"}
      },
      {
        "column": "status",
        "expression": "==",
        "value": "completed"
      }
    ]
  }
}
```

This filters to rows where:
- `fecha_escuchado >= fecha_creacion` **AND**
- `status == "completed"`

## Error Handling

### Referenced Column Not Found
If you reference a column that doesn't exist in the DataFrame:

```json
{
  "filter": [
    {
      "column": "value_a",
      "expression": ">",
      "value": {"$column": "nonexistent_column"}
    }
  ]
}
```

**Result**: QueryException with message:
```
Referenced column 'nonexistent_column' not found in DataFrame.
```

### Primary Column Not Found
Regular column validation still applies:

```json
{
  "filter": [
    {
      "column": "nonexistent_column",
      "expression": ">",
      "value": {"$column": "other_column"}
    }
  ]
}
```

**Result**: QueryException with message:
```
tFilter: Column nonexistent_column not found in DataFrame.
```

## Implementation Details

- The syntax `{"$column": "..."}` disambiguates column references from literal string values
- This prevents conflicts if a string value happens to match a column name
- Column references are validated against the DataFrame at runtime
- The generated condition is evaluated using Pandas' standard filtering mechanism

## Examples

### Date Comparison (Podcasts)
```json
{
  "filter": [
    {
      "column": "fecha_escuchado",
      "expression": ">=",
      "value": {"$column": "fecha_podcast"}
    }
  ]
}
```

### Numeric Comparison
```json
{
  "filter": [
    {
      "column": "valor_actual",
      "expression": "<",
      "value": {"$column": "valor_limite"}
    }
  ]
}
```

### String Comparison
```json
{
  "filter": [
    {
      "column": "nombre_usuario",
      "expression": "==",
      "value": {"$column": "nombre_esperado"}
    }
  ]
}
```

### Range Check (Numeric)
```json
{
  "filter": [
    {
      "column": "edad",
      "expression": ">=",
      "value": {"$column": "edad_minima"}
    },
    {
      "column": "edad",
      "expression": "<=",
      "value": {"$column": "edad_maxima"}
    }
  ]
}
```

## Performance Considerations

- Column-to-column filters are as efficient as scalar filters (both use Pandas' boolean indexing)
- No intermediate columns are created
- The operation is vectorized and optimized by Pandas
- Suitable for large datasets
