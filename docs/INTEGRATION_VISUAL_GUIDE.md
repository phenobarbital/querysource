# Join + Column Filter: Visual Integration Guide

## The Complete Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        MultiQuery Execution Flow                        │
└─────────────────────────────────────────────────────────────────────────┘

STEP 1: Run Queries in Parallel
┌──────────────────────┐          ┌──────────────────────┐
│   Query: "calls"     │          │ Query: "podcasts"    │
│                      │          │                      │
│  slug: get-calls     │          │ slug: get-podcasts   │
│                      │          │                      │
└──────────┬───────────┘          └──────────┬───────────┘
           │                                 │
           └────────────┬────────────────────┘
                        │
                        ▼
              ┌─────────────────────┐
              │  Dictionary of DFs:  │
              │  {                  │
              │    "calls": df1,    │
              │    "podcasts": df2  │
              │  }                  │
              └─────────────────────┘

STEP 2: Join Operator
┌──────────────────────────────────────────────────────────────────────┐
│  Join (type: "inner", on: "usuario_id")                            │
│                                                                      │
│  df1 (calls)     + df2 (podcasts)  ──►  merged_df                  │
│  ─────────────────────────────────      ──────────                │
│  id_llamada            id_podcast  (common columns preserved)      │
│  usuario_id   ◄────►   usuario_id                                 │
│  fecha_llamada         fecha_creacion                             │
│  duracion_llamada      duracion_podcast                           │
│                        fecha_escuchado                            │
└──────────────────────────────────────────────────────────────────────┘

STEP 3: Column Filter Operator
┌──────────────────────────────────────────────────────────────────────┐
│  Filter (column-to-column comparisons)                              │
│                                                                      │
│  Input: merged_df (from Join)                                      │
│         └─► Has columns from BOTH tables                           │
│                                                                      │
│  Filter Conditions:                                                 │
│  ├─ fecha_escuchado >= fecha_creacion                              │
│  ├─ duracion_llamada < duracion_podcast                            │
│  └─ region == "LATAM" (scalar condition OK too)                    │
│                                                                      │
│  Output: filtered_df (rows matching ALL conditions)                │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Example: Step-by-Step Data Transformation

### STEP 1: Call Data (Query 1)
```
┌────────────┬────────────┬──────────────┬──────────────┐
│ id_llamada │ usuario_id │ fecha_llamada│ duracion_lld │
├────────────┼────────────┼──────────────┼──────────────┤
│     1      │    100     │  2024-01-15  │      45      │
│     2      │    101     │  2024-01-20  │      30      │
│     3      │    100     │  2024-02-01  │      60      │
└────────────┴────────────┴──────────────┴──────────────┘
```

### STEP 2: Podcast Data (Query 2)
```
┌────────────┬────────────┬──────────────┬──────────────┬──────────────┐
│ id_podcast │ usuario_id │ fecha_creacion│ duracion_pod │ fecha_escucha│
├────────────┼────────────┼──────────────┼──────────────┼──────────────┤
│    200     │    100     │  2024-01-10  │      90      │  2024-01-20  │
│    201     │    101     │  2024-01-25  │      45      │  2024-01-30  │
│    202     │    100     │  2024-02-02  │      60      │  2024-02-05  │
└────────────┴────────────┴──────────────┴──────────────┴──────────────┘
```

### STEP 3: After Join (type: "inner", on: "usuario_id")
```
┌────────┬────────────┬──────────┬──────────┬────────┬──────────┬──────────┬──────────┐
│   id   │usuario_id  │ fecha_ll │duracion_l│ id_pod │fecha_cre │duracion_p│fecha_esc │
├────────┼────────────┼──────────┼──────────┼────────┼──────────┼──────────┼──────────┤
│   1    │   100      │ 2024-01-15│   45    │  200   │ 2024-01-10│   90    │2024-01-20│
│   1    │   100      │ 2024-01-15│   45    │  202   │ 2024-02-02│   60    │2024-02-05│
│   2    │   101      │ 2024-01-20│   30    │  201   │ 2024-01-25│   45    │2024-01-30│
│   3    │   100      │ 2024-02-01│   60    │  200   │ 2024-01-10│   90    │2024-01-20│
│   3    │   100      │ 2024-02-01│   60    │  202   │ 2024-02-02│   60    │2024-02-05│
└────────┴────────────┴──────────┴──────────┴────────┴──────────┴──────────┴──────────┘
```

### STEP 4: Column Filter Applied

**Filter Condition:**
```json
{
  "filter": [
    {
      "column": "fecha_escuchado",
      "expression": ">=",
      "value": {"$column": "fecha_creacion"}
    },
    {
      "column": "duracion_llamada",
      "expression": "<",
      "value": {"$column": "duracion_podcast"}
    }
  ]
}
```

**Evaluation per Row:**
```
Row 1: 2024-01-20 >= 2024-01-10 ✓ AND 45 < 90 ✓  ──► KEEP
Row 2: 2024-02-05 >= 2024-02-02 ✓ AND 45 < 60 ✓  ──► KEEP
Row 3: 2024-01-30 >= 2024-01-25 ✓ AND 30 < 45 ✓  ──► KEEP
Row 4: 2024-01-20 >= 2024-01-10 ✓ AND 60 < 90 ✓  ──► KEEP
Row 5: 2024-02-05 >= 2024-02-02 ✓ AND 60 < 60 ✗  ──► DROP
```

**Final Result:**
```
┌────────┬────────────┬──────────┬──────────┬────────┬──────────┬──────────┬──────────┐
│   id   │usuario_id  │ fecha_ll │duracion_l│ id_pod │fecha_cre │duracion_p│fecha_esc │
├────────┼────────────┼──────────┼──────────┼────────┼──────────┼──────────┼──────────┤
│   1    │   100      │ 2024-01-15│   45    │  200   │ 2024-01-10│   90    │2024-01-20│  ✓
│   1    │   100      │ 2024-01-15│   45    │  202   │ 2024-02-02│   60    │2024-02-05│  ✓
│   2    │   101      │ 2024-01-20│   30    │  201   │ 2024-01-25│   45    │2024-01-30│  ✓
│   3    │   100      │ 2024-02-01│   60    │  200   │ 2024-01-10│   90    │2024-01-20│  ✓
└────────┴────────────┴──────────┴──────────┴────────┴──────────┴──────────┴──────────┘
```

---

## JSON Request Example

```json
POST /query/multi

{
  "queries": {
    "calls": {
      "slug": "get-calls"
    },
    "podcasts": {
      "slug": "get-podcasts"
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
        "value": {"$column": "fecha_creacion"}
      },
      {
        "column": "duracion_llamada",
        "expression": "<",
        "value": {"$column": "duracion_podcast"}
      }
    ]
  }
}
```

---

## Architecture: What Happens Under the Hood

### 1. Query Execution (Parallel)
```python
# Handler receives request
# Launches ThreadQuery for "calls" and "podcasts" in parallel
tasks = {
    "calls": ThreadQuery(...),
    "podcasts": ThreadQuery(...)
}

# Wait for both to complete
result = {
    "calls": df_calls,          # 3 rows
    "podcasts": df_podcasts      # 3 rows
}
```

### 2. Join Execution
```python
# MultiQS.query() detects 'Join' in options
# Join operator receives: {"calls": df_calls, "podcasts": df_podcasts}

join_op = Join(
    data=result,
    left="calls",
    right="podcasts",
    on="usuario_id",
    type="inner"
)

merged = await join_op.run()
# Returns: {
#   "calls.podcasts": df_merged  # Cartesian product on usuario_id
# }
```

### 3. Filter Execution
```python
# MultiQS.query() detects 'Filter' in options
# Filter operator receives the merged DataFrame

filter_op = Filter(
    data=merged,  # From join result
    filter=[
        {
            "column": "fecha_escuchado",
            "expression": ">=",
            "value": {"$column": "fecha_creacion"}
        },
        ...
    ]
)

# build_condition() recognizes {"$column": "..."}
# Generates: "(df['fecha_escuchado'] >= df['fecha_creacion'])"
# Applies via: df.loc[eval(condition)]

filtered = await filter_op.run()
```

---

## Key Integration Points

### 1. Column Detection
- **Before Join**: Columns are from their individual tables
- **After Join**: Columns from BOTH tables available
- **Filter sees merged columns** → Can compare across tables

### 2. Type Compatibility
```
Same type comparisons:
├─ date >= date        ✓
├─ numeric > numeric   ✓
├─ string == string    ✓
└─ mixed types         ? (Pandas behavior)

Best practice:
└─ Ensure both columns have compatible types
   (PostgreSQL cast, or data transformation in query)
```

### 3. NULL/NaN Handling
```python
# Pandas behavior with NaN in comparisons:
np.NaN >= any_value  # → False
any_value >= np.NaN  # → False

# Filter will exclude these rows (standard SQL semantics)
# If you need different behavior, pre-clean data in Query
```

---

## Performance Profile

### Join
- **Operation**: `pd.merge(df1, df2, on=key)`
- **Complexity**: O(n log n) with typical SQL-like indexes
- **Result size**: depends on join type and key cardinality

### Filter (Column-to-Column)
- **Operation**: `df.loc[df[col1] >= df[col2]]` (vectorized)
- **Complexity**: O(n) single pass over data
- **Memory**: One boolean mask array
- **Cost vs scalar filter**: SAME (both vectorized)

### Combined
```
Total time ≈ T(join) + T(filter)
           (no additional overhead from column-column reference)
```

---

## Error Scenarios

### Scenario 1: Column Not Found in Merged Data
```
User Filter:
{
  "column": "nonexistent_field",
  "expression": ">=",
  "value": {"$column": "fecha"}
}

Error:
QueryException: tFilter: Column nonexistent_field not found in DataFrame

Reason:
├─ Column doesn't exist in either source table
└─ Not created by join

Solution:
├─ Check Join produces expected columns
├─ Use Info operator to inspect merged schema
└─ Rename in Query if needed
```

### Scenario 2: Referenced Column Not Found
```
User Filter:
{
  "column": "fecha_escuchado",
  "expression": ">=",
  "value": {"$column": "fecha_inexistente"}
}

Error:
QueryException: Referenced column 'fecha_inexistente' not found in DataFrame

Solution:
├─ Verify column name exists in merged data
├─ Check Join key column names
└─ May need alias in Source Query
```

### Scenario 3: Cardinality Explosion (Too Many Rows)
```
Situation:
├─ Join type: "inner"
├─ Left table: 1,000 rows
├─ Right table: 1,000 rows
├─ Join key not unique
└─ Result: 10,000 rows (expected if key cardinality is low)

Check:
├─ Use Info to see row count after join
├─ Verify Join type is correct
├─ Consider filter earlier or change join type
```

---

## Testing and Validation

### 1. Check Join Output
```json
{
  "queries": { ... },
  "Join": { ... },
  "Info": {}
}
```
Output shows:
- Row count
- Column names
- Data types
- Sample values

### 2. Validate Filter Conditions
```json
{
  "queries": { ... },
  "Join": { ... },
  "Filter": {
    "filter": [
      {
        "column": "col1",
        "expression": ">=",
        "value": {"$column": "col2"}
      }
    ]
  },
  "Info": {}
}
```
Check:
- Result row count
- Spot check a few rows manually
- Verify condition logic

---

## Best Practices

1. **Start Simple**
   - Single join key
   - Clear column names
   - One filter condition at a time

2. **Use Info to Debug**
   - After each major step
   - Check column names after join
   - Verify data types match

3. **Filter Logic**
   - Put most restrictive conditions first
   - Use scalar filters before column filters
   - Consider join type impact on result size

4. **Data Quality**
   - Ensure join key is unique/valid
   - Handle NULLs before filtering
   - Cast types in source Query if needed

5. **Naming**
   - Use clear, distinct column names
   - Avoid suffixes that match `_left`, `_right`
   - Document expected schema

---

## Comparison: Old vs New Approach

### Before Column Filters (Create Intermediate Column)
```json
{
  "queries": { ... },
  "Join": { ... },
  "Transform": [
    {
      "AddColumn": {
        "name": "can_listen",
        "expression": "fecha_escuchado >= fecha_creacion"
      }
    }
  ],
  "Filter": {
    "filter": [
      {
        "column": "can_listen",
        "expression": "==",
        "value": true
      }
    ]
  }
}
```

### After Column Filters (Direct Comparison)
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

**Benefits:**
- ✅ Cleaner JSON
- ✅ No intermediate columns
- ✅ Direct intent
- ✅ Better performance (one operation vs two)

---

## See Also

- [Column Filter Reference](./COLUMN_FILTER_EXAMPLE.md)
- [Join + Column Filter Integration Guide](./JOIN_AND_COLUMN_FILTER_INTEGRATION.md)
- [Test Examples](./tests/test_join_with_column_filter.py)
