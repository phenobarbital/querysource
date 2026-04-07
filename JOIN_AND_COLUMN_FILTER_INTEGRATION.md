# Join + Column Filter Integration Guide

## Overview

The **Join** operator creates unified DataFrames by merging data from multiple query sources. The **Column Filter** can then compare columns from both the left and right tables using the `{"$column": "name"}` syntax.

This enables powerful post-join filtering without creating intermediate columns.

---

## Architecture

### Flow Diagram

```
Query 1 (calls)      Query 2 (podcasts)
      ↓                        ↓
   DataFrame              DataFrame
      ↓                        ↓
      └──────────┬─────────────┘
                 ↓
            [Join Operator]
         (inner/left/right/outer)
                 ↓
         Merged DataFrame
    (columns from both tables)
                 ↓
          [Column Filter]
  (filter using cross-table comparisons)
                 ↓
          Filtered Result
```

---

## Step 1: Queries

Define your data sources:

```json
{
  "queries": {
    "llamadas": {
      "slug": "get-calls"
    },
    "podcasts": {
      "slug": "get-podcasts"
    }
  }
}
```

**llamadas DataFrame:**
```
| id_llamada | usuario_id | fecha_llamada | duracion |
|------------|------------|---------------|----------|
| 1          | 100        | 2024-01-15    | 45       |
| 2          | 101        | 2024-01-20    | 30       |
| 3          | 100        | 2024-02-01    | 60       |
```

**podcasts DataFrame:**
```
| id_podcast | usuario_id | fecha_creacion | duracion_total | fecha_escuchado |
|------------|------------|----------------|----------------|-----------------|
| 200        | 100        | 2024-01-10     | 90             | 2024-01-20      |
| 201        | 101        | 2024-01-25     | 45             | 2024-01-30      |
| 202        | 100        | 2024-02-02     | 60             | 2024-02-05      |
```

---

## Step 2: Join

The Join operator merges DataFrames by common column(s):

```json
{
  "queries": { ... },
  "Join": {
    "left": "llamadas",
    "right": "podcasts",
    "on": "usuario_id",
    "type": "inner"
  }
}
```

**Merged DataFrame:**
```
| id_llamada | usuario_id | fecha_llamada | duracion | id_podcast | fecha_creacion | duracion_total | fecha_escuchado |
|------------|------------|---------------|----------|------------|----------------|----------------|-----------------|
| 1          | 100        | 2024-01-15    | 45       | 200        | 2024-01-10     | 90             | 2024-01-20      |
| 1          | 100        | 2024-01-15    | 45       | 202        | 2024-02-02     | 60             | 2024-02-05      |
| 2          | 101        | 2024-01-20    | 30       | 201        | 2024-01-25     | 45             | 2024-01-30      |
| 3          | 100        | 2024-02-01    | 60       | 200        | 2024-01-10     | 90             | 2024-01-20      |
| 3          | 100        | 2024-02-01    | 60       | 202        | 2024-02-02     | 60             | 2024-02-05      |
```

---

## Step 3: Column Filter

Now you can filter using **column-to-column comparisons** from the merged data:

### Example 1: Podcasts listened AFTER they were created

```json
{
  "queries": { ... },
  "Join": { ... },
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

**Result:** All rows (all podcasts were listened to after creation)

### Example 2: Calls made BEFORE podcast was created

```json
{
  "queries": { ... },
  "Join": { ... },
  "Filter": {
    "filter": [
      {
        "column": "fecha_llamada",
        "expression": "<",
        "value": {"$column": "fecha_creacion"}
      }
    ]
  }
}
```

**Result:**
```
| id_llamada | usuario_id | fecha_llamada | duracion | id_podcast | fecha_creacion | duracion_total | fecha_escuchado |
|------------|------------|---------------|----------|------------|----------------|----------------|-----------------|
| 1          | 100        | 2024-01-15    | 45       | 200        | 2024-01-10     | 90             | 2024-01-20      | ✗
| 1          | 100        | 2024-01-15    | 45       | 202        | 2024-02-02     | 60             | 2024-02-05      | ✓
| 2          | 101        | 2024-01-20    | 30       | 201        | 2024-01-25     | 45             | 2024-01-30      | ✓
| 3          | 100        | 2024-02-01    | 60       | 200        | 2024-01-10     | 90             | 2024-01-20      | ✗
| 3          | 100        | 2024-02-01    | 60       | 202        | 2024-02-02     | 60             | 2024-02-05      | ✓
```

### Example 3: Complex: Multiple conditions with mixed column/scalar filters

```json
{
  "queries": { ... },
  "Join": { ... },
  "Filter": {
    "filter": [
      {
        "column": "fecha_escuchado",
        "expression": ">=",
        "value": {"$column": "fecha_creacion"}
      },
      {
        "column": "duracion",
        "expression": "<",
        "value": {"$column": "duracion_total"}
      },
      {
        "column": "usuario_id",
        "expression": "==",
        "value": 100
      }
    ]
  }
}
```

**Conditions:**
- `fecha_escuchado >= fecha_creacion` (listened after creation)
- `duracion < duracion_total` (call duration shorter than podcast)
- `usuario_id == 100` (scalar filter)

---

## Complete Example: Real-World Scenario

### Use Case
Find all user interactions where:
1. User listened to the podcast AFTER it was created
2. The listening happened AFTER the call was made
3. The call was with a specific region

```json
{
  "queries": {
    "llamadas": {
      "slug": "get-calls"
    },
    "podcasts": {
      "slug": "get-podcasts"
    }
  },
  "Join": {
    "left": "llamadas",
    "right": "podcasts",
    "on": "usuario_id",
    "type": "inner"
  },
  "Filter": {
    "filter": [
      {
        "column": "fecha_escuchado",
        "expression": ">=",
        "value": {"$column": "fecha_creacion"}
      },
      {
        "column": "fecha_escuchado",
        "expression": ">=",
        "value": {"$column": "fecha_llamada"}
      },
      {
        "column": "region",
        "expression": "==",
        "value": "LATAM"
      }
    ]
  }
}
```

---

## Join Types and Column Naming

### Join Types Supported

| Type | Behavior | Example |
|------|----------|---------|
| `inner` | Only matching rows | Default, recommended for most cases |
| `left` | All from left, matching from right | Keep all calls, add podcasts if available |
| `right` | Matching from left, all from right | Keep all podcasts, add calls if available |
| `outer` | All rows from both | Complete history |

### Column Name Conflicts

When joining on the same column names, Pandas adds suffixes to duplicates:

**Without explicit column matching:**
```json
{
  "Join": {
    "left": "llamadas",
    "right": "podcasts"
  }
}
```

**With explicit 'on':**
```json
{
  "Join": {
    "left": "llamadas",
    "right": "podcasts",
    "on": "usuario_id"
  }
}
```

**With multiple join keys:**
```json
{
  "Join": {
    "left": "llamadas",
    "right": "podcasts",
    "on": ["usuario_id", "fecha_mes"]
  }
}
```

### Suffix Handling

If both tables have a column with the same name (not the join key), Pandas appends suffixes:
- Left table: `_left`
- Right table: `_right`

The Join operator **automatically removes** `_left` columns, keeping only `_right` versions.

Example:
```
Original: llamadas.duración, podcasts.duración
After join: duración_left, duración_right
After cleanup: duración (from podcasts, renamed to duración)
```

**In Filter, use the final name without suffix:**
```json
{
  "Filter": {
    "filter": [
      {
        "column": "duración",
        "expression": "<",
        "value": {"$column": "duracion_total"}
      }
    ]
  }
}
```

---

## Advanced Patterns

### Pattern 1: Range Validation

Check if a value from one table falls within a range from another:

```json
{
  "Filter": {
    "filter": [
      {
        "column": "edad_usuario",
        "expression": ">=",
        "value": {"$column": "edad_minima_podcast"}
      },
      {
        "column": "edad_usuario",
        "expression": "<=",
        "value": {"$column": "edad_maxima_podcast"}
      }
    ]
  }
}
```

### Pattern 2: Time Window Analysis

Find events within a time window:

```json
{
  "Filter": {
    "filter": [
      {
        "column": "fecha_escucha",
        "expression": ">=",
        "value": {"$column": "fecha_inicio_ventana"}
      },
      {
        "column": "fecha_escucha",
        "expression": "<",
        "value": {"$column": "fecha_fin_ventana"}
      }
    ]
  }
}
```

### Pattern 3: Numeric Comparisons

Compare numeric values from both tables:

```json
{
  "Filter": {
    "filter": [
      {
        "column": "valor_real",
        "expression": ">",
        "value": {"$column": "valor_esperado"}
      },
      {
        "column": "diferencia_porcentaje",
        "expression": "<=",
        "value": 10
      }
    ]
  }
}
```

### Pattern 4: String Comparisons

Compare string columns (exact match):

```json
{
  "Filter": {
    "filter": [
      {
        "column": "codigo_usuario",
        "expression": "==",
        "value": {"$column": "codigo_esperado"}
      },
      {
        "column": "idioma",
        "expression": "==",
        "value": "es"
      }
    ]
  }
}
```

---

## Error Handling

### Column Not Found After Join

If you reference a column that doesn't exist in the joined result:

```json
{
  "Filter": {
    "filter": [
      {
        "column": "nonexistent_field",
        "expression": ">=",
        "value": {"$column": "fecha_creacion"}
      }
    ]
  }
}
```

**Error:**
```
QueryException: tFilter: Column nonexistent_field not found in DataFrame.
```

**Solution:** Check the actual column names in the merged DataFrame. Use `Get` operator to inspect:

```json
{
  "queries": { ... },
  "Join": { ... },
  "Info": {}
}
```

This will print the column names and data types.

### Referenced Column Not Found

If you reference a non-existent column in `$column`:

```json
{
  "Filter": {
    "filter": [
      {
        "column": "fecha_escuchado",
        "expression": ">=",
        "value": {"$column": "fecha_inexistente"}
      }
    ]
  }
}
```

**Error:**
```
QueryException: Referenced column 'fecha_inexistente' not found in DataFrame.
```

---

## Performance Considerations

### Join Performance

- **Inner Join** (smallest result): Fastest, use when you only need matching rows
- **Left Join** (preserve all left): Fast, good for "keep all primary records"
- **Right Join** (preserve all right): Medium, less common
- **Outer Join** (all rows): Slowest, largest result set

### Filter Performance

- Column-to-column filters use Pandas' vectorized boolean indexing
- **No performance penalty** vs scalar filters
- Applied **after join**, so filtering on joined data doesn't increase join complexity
- Filters are applied **in order**, so put most restrictive conditions first

### Optimization Tips

1. **Join before Filter**: Done automatically by MultiQuery
2. **Filter early**: More restrictive conditions first
3. **Use Inner Join when possible**: Reduces data size before filtering
4. **Index on join columns**: Implicit in Pandas merge

---

## Complete Working Example

```json
{
  "queries": {
    "calls": {
      "slug": "qry-calls-by-region"
    },
    "podcasts": {
      "slug": "qry-podcasts-metadata"
    }
  },
  "Join": {
    "left": "calls",
    "right": "podcasts",
    "on": "usuario_id",
    "type": "inner"
  },
  "Filter": {
    "filter": [
      {
        "column": "fecha_escuchado",
        "expression": ">=",
        "value": {"$column": "fecha_podcast"}
      },
      {
        "column": "duracion_llamada",
        "expression": "<",
        "value": {"$column": "duracion_podcast"}
      },
      {
        "column": "estado",
        "expression": "==",
        "value": "completado"
      }
    ]
  },
  "GroupBy": {
    "group_by": ["usuario_id"],
    "agg": {
      "duracion_llamada": "sum",
      "duracion_podcast": "avg"
    }
  }
}
```

---

## Troubleshooting

### Empty Result After Filter

If you get no results, check:

1. **Join produced data**: Add `"Info": {}` before Filter to inspect
2. **Column names**: Check for `_left`/`_right` suffixes
3. **Data types**: Dates might be strings instead of datetime
4. **Filter logic**: Is your condition too restrictive?

### Unexpected Column Names

```json
{
  "Join": {
    "left": "llamadas",
    "right": "podcasts"
  },
  "Info": {}
}
```

This prints all columns and their types, helping you write correct filter conditions.

### Join Produces Cartesian Product

If you didn't specify `on` and get many more rows than expected:

```json
{
  "Join": {
    "left": "llamadas",
    "right": "podcasts",
    "on": "usuario_id"  ← Add this
  }
}
```

---

## FAQ

**Q: Can I filter before joining?**  
A: No, filters work on single DataFrames. Join first, then filter the merged result.

**Q: Can I compare with a constant AND a column?**  
A: Yes, use multiple filter conditions (AND logic):
```json
{
  "filter": [
    {"column": "fecha", "expression": ">=", "value": {"$column": "fecha_base"}},
    {"column": "fecha", "expression": "<", "value": "2024-12-31"}
  ]
}
```

**Q: What if columns have the same name?**  
A: Join handles this with suffixes. Use the final name in filters (without suffix if it was removed).

**Q: Can I use OR logic?**  
A: Not in the standard Filter. All conditions use AND. Create multiple queries for OR.

**Q: Is this slower than scalar filters?**  
A: No, column filters use Pandas' vectorized operations (same as scalar filters).

---

## See Also

- [Column Filter Reference](./COLUMN_FILTER_EXAMPLE.md)
- [Join Operator Documentation](./querysource/queries/multi/operators/Join.py)
- [Pandas Merge Documentation](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.merge.html)
