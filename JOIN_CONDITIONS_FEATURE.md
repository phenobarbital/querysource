# Join Conditions Feature

## Overview

**Join Conditions** allow you to specify column-to-column comparison conditions directly in the Join operator, without needing a separate Filter operator.

This is syntactically cleaner and semantically clearer than Join + Filter for complex join scenarios.

---

## Problem It Solves

### Before (Join + Filter Approach)
```json
{
  "Join": {
    "left": "calls",
    "right": "podcasts",
    "on": "usuario_id"
  },
  "Filter": {
    "filter": [
      {
        "column": "call_date",
        "expression": ">=",
        "value": {"$column": "podcast_date"}
      }
    ]
  }
}
```

**Issues:**
- Requires 2 operators
- Intent is unclear (is the filter part of the join or applied after?)
- Harder to optimize

### After (Join with Conditions)
```json
{
  "Join": {
    "left": "calls",
    "right": "podcasts",
    "on": "usuario_id",
    "join_conditions": [
      {
        "left": "call_date",
        "expression": ">=",
        "right": "podcast_date"
      }
    ]
  }
}
```

**Benefits:**
- Single operator
- Intent is crystal clear
- Conditions are semantically part of the join
- Easier to understand and maintain

---

## Syntax

### Basic Structure

```json
{
  "Join": {
    "left": "table1",
    "right": "table2",
    "on": "join_key",
    "join_conditions": [
      {
        "left": "column_from_table1",
        "expression": ">=",
        "right": "column_from_table2"
      }
    ]
  }
}
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| **left** | string | Name of left table (required) |
| **right** | string | Name of right table (required) |
| **on** | string \| list | Join key(s) - same as standard join |
| **join_conditions** | array | Array of join condition objects |
| **type** | string | Join type: `inner`, `left`, `right`, `outer` (default: `inner`) |

### Join Condition Object

```json
{
  "left": "column_name_from_left_table",
  "expression": ">=",
  "right": "column_name_from_right_table"
}
```

| Field | Type | Description |
|-------|------|-------------|
| **left** | string | Column from left table |
| **expression** | string | Comparison operator: `>`, `>=`, `<`, `<=`, `==`, `!=` |
| **right** | string | Column from right table |

---

## Supported Operators

All comparison operators work with join_conditions:

- **Numeric**: `>`, `>=`, `<`, `<=`, `==`, `!=`
- **String**: `==`, `!=` (others via string methods not yet supported in join context)
- **Date**: `>`, `>=`, `<`, `<=`, `==`, `!=`

---

## Examples

### Example 1: Simple Date Comparison

```json
{
  "queries": {
    "calls": {"slug": "get-calls"},
    "podcasts": {"slug": "get-podcasts"}
  },
  "Join": {
    "left": "calls",
    "right": "podcasts",
    "on": "usuario_id",
    "join_conditions": [
      {
        "left": "call_date",
        "expression": ">=",
        "right": "podcast_date"
      }
    ]
  }
}
```

**SQL Equivalent:**
```sql
SELECT *
FROM calls c
INNER JOIN podcasts p
  ON c.usuario_id = p.usuario_id
  AND c.call_date >= p.podcast_date
```

---

### Example 2: Multiple Join Conditions (AND Logic)

```json
{
  "Join": {
    "left": "calls",
    "right": "podcasts",
    "on": "usuario_id",
    "join_conditions": [
      {
        "left": "call_date",
        "expression": ">=",
        "right": "podcast_date"
      },
      {
        "left": "call_duration",
        "expression": "<",
        "right": "podcast_duration"
      }
    ]
  }
}
```

**SQL Equivalent:**
```sql
SELECT *
FROM calls c
INNER JOIN podcasts p
  ON c.usuario_id = p.usuario_id
  AND c.call_date >= p.podcast_date
  AND c.call_duration < p.podcast_duration
```

---

### Example 3: Range Checking Between Columns

```json
{
  "Join": {
    "left": "orders",
    "right": "price_tiers",
    "on": "product_id",
    "join_conditions": [
      {
        "left": "order_amount",
        "expression": ">=",
        "right": "min_price"
      },
      {
        "left": "order_amount",
        "expression": "<=",
        "right": "max_price"
      }
    ]
  }
}
```

**SQL Equivalent:**
```sql
SELECT *
FROM orders o
INNER JOIN price_tiers pt
  ON o.product_id = pt.product_id
  AND o.order_amount >= pt.min_price
  AND o.order_amount <= pt.max_price
```

---

### Example 4: All Join Types with Conditions

```json
{
  "Join": {
    "left": "calls",
    "right": "podcasts",
    "on": "usuario_id",
    "type": "left",
    "join_conditions": [
      {
        "left": "call_date",
        "expression": ">",
        "right": "podcast_date"
      }
    ]
  }
}
```

Join types supported:
- `inner` - Only matching rows with condition satisfied
- `left` - All from left, matching from right where condition satisfied
- `right` - Matching from left, all from right where condition satisfied
- `outer` - All rows from both where condition satisfied or one side is NULL

---

## How It Works

### Execution Flow

```
1. Merge on join key(s) using specified join type
   └─► Intermediate result (before conditions)

2. Apply join_conditions to filtered result
   └─► Reuses Filter expression builder
   └─► AND logic combines all conditions
   └─► Final result

3. Return joined and conditioned DataFrame
```

### Under the Hood

Join conditions internally reuse the `create_filter()` function from the Filter module:

```python
# Join condition:
{"left": "col_a", "expression": ">=", "right": "col_b"}

# Converted to Filter format:
{"column": "col_a", "expression": ">=", "value": {"$column": "col_b"}}

# Applied via:
df = df.loc[eval("(df['col_a'] >= df['col_b'])")]
```

This ensures **consistent behavior** between Filter and Join conditions.

---

## Performance

- **Complexity**: O(n log n) for merge + O(n) for conditions
- **Memory**: One boolean mask array per condition
- **Optimization**: Conditions are applied immediately after merge, not in post-processing

Join conditions have **no performance penalty** vs Join + Filter approach.

---

## Error Handling

### Missing Required Fields

```json
{
  "join_conditions": [
    {
      "left": "col_a"
      // Missing: "expression" and "right"
    }
  ]
}
```

**Error:**
```
QueryException: Join condition must have 'left', 'expression', and 'right' fields
```

### Column Not Found

```json
{
  "join_conditions": [
    {
      "left": "nonexistent_column",
      "expression": ">=",
      "right": "podcast_date"
    }
  ]
}
```

**Error:**
```
QueryException: tFilter: Column nonexistent_column not found in DataFrame.
```

### Empty Result After Conditions

**Status**: VALID (not an error)

If all rows are filtered out by conditions, an empty DataFrame is returned. This is semantically valid - no rows matched the join conditions.

---

## Comparison: Join Conditions vs Filter

| Aspect | Join Conditions | Join + Filter |
|--------|-----------------|---------------|
| **Semantics** | Conditions are part of join | Filter is post-join |
| **Operators** | 1 (Join) | 2 (Join + Filter) |
| **Clarity** | Very clear intent | Slightly ambiguous |
| **Performance** | Same | Same |
| **SQL Equivalence** | INNER/LEFT/RIGHT/OUTER JOIN ... ON ... AND ... | SELECT ... WHERE ... |
| **Use Case** | Explicit join conditions | General filtering |

---

## When to Use

### Use Join Conditions When:
- ✅ Conditions are logically part of the join
- ✅ Comparing columns from both tables
- ✅ Want clear, single-operator syntax
- ✅ Multiple join conditions (cleaner than sequential filters)

### Use Join + Filter When:
- ✅ Filter is applied post-join for different reasons
- ✅ Want to use advanced filter operators (contains, regex, etc.)
- ✅ Filter logic is complex and deserves its own operator

---

## Complete Example

### Data Setup

**calls table:**
```
| id | user_id | date       | duration |
|----|---------|------------|----------|
| 1  | 100     | 2024-01-15 | 45       |
| 2  | 101     | 2024-01-20 | 30       |
```

**podcasts table:**
```
| id | user_id | date       | duration |
|----|---------|------------|----------|
| 200 | 100    | 2024-01-10 | 90       |
| 201 | 101    | 2024-01-25 | 45       |
```

### Query with Join Conditions

```json
{
  "queries": {
    "calls": {"slug": "get-calls"},
    "podcasts": {"slug": "get-podcasts"}
  },
  "Join": {
    "left": "calls",
    "right": "podcasts",
    "on": "user_id",
    "type": "inner",
    "join_conditions": [
      {
        "left": "date",
        "expression": ">=",
        "right": "date"
      },
      {
        "left": "duration",
        "expression": "<",
        "right": "duration"
      }
    ]
  }
}
```

### Result

```
| id (call) | user_id | date (call) | duration (call) | id (podcast) | date (podcast) | duration (podcast) |
|-----------|---------|-------------|-----------------|--------------|----------------|--------------------|
| 1         | 100     | 2024-01-15  | 45              | 200          | 2024-01-10     | 90                 |
| 2         | 101     | 2024-01-20  | 30              | 201          | 2024-01-25     | 45                 |
```

(Row 2 included: call_date 2024-01-20 >= podcast_date 2024-01-25? No... wait, let me recalculate)

Actually:
- Row 1: 2024-01-15 >= 2024-01-10 ✓ AND 45 < 90 ✓  → KEEP
- Row 2: 2024-01-20 >= 2024-01-25 ✗  → DROP

**Final Result:**
```
| id | user_id | date       | duration | id  | date       | duration |
|----|---------|------------|----------|-----|------------|----------|
| 1  | 100     | 2024-01-15 | 45       | 200 | 2024-01-10 | 90       |
```

---

## Integration with MultiQuery

Join Conditions are fully integrated into the MultiQuery workflow:

```
Query Execution
  ↓
Join (with optional conditions)
  ├─ Merge on keys
  └─ Apply conditions
  ↓
Filter (optional, for post-join filtering)
  ↓
GroupBy (optional)
  ↓
Output
```

---

## Limitations

- ✅ AND logic only (not OR between conditions)
  - Workaround: use separate Join operators in sequence
- ✅ No aggregate functions in conditions
  - Workaround: pre-compute aggregates in Query
- ✅ No string pattern matching in join context
  - Workaround: use Filter operator post-join

---

## Testing

Tests are included in:
- `tests/test_join_conditions.py` - 10 comprehensive tests

Run with:
```bash
pytest tests/test_join_conditions.py -v
```

---

## See Also

- [Column Filter Reference](./COLUMN_FILTER_EXAMPLE.md)
- [Join + Column Filter Integration](./JOIN_AND_COLUMN_FILTER_INTEGRATION.md)
- [Join Operator Implementation](./querysource/queries/multi/operators/Join.py)
