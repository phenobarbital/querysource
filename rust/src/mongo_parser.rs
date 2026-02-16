// Copyright (C) 2018-present Jesus Lara
//
// mongo_parser.rs — MongoDB/DocumentDB query builder with rayon parallelism.
// Unlike SQL parsers, MongoDB queries are dict-based, so these functions
// return Python dicts/lists via PyO3 rather than strings.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};
use rayon::prelude::*;
use regex::Regex;
use once_cell::sync::Lazy;

// ---------------------------------------------------------------------------
// Shared types for parallel processing
// ---------------------------------------------------------------------------

/// Represents a single filter entry extracted from Python (GIL-free).
#[derive(Debug, Clone)]
struct MongoFilterEntry {
    _key: String,
    field_name: String,
    suffix: String,
    value: MongoValue,
    field_type: Option<String>,
}

/// Represents the possible value types from Python filter dict.
#[derive(Debug, Clone)]
enum MongoValue {
    Str(String),
    Int(i64),
    Float(f64),
    Bool(bool),
    None,
    Dict { op: String, val: Box<MongoValue> },
    List(Vec<MongoValue>),
    ListWithOp { op: String, val: Box<MongoValue> },
}

/// Represents a processed MongoDB condition (GIL-free).
#[derive(Debug, Clone)]
enum MongoCondition {
    /// Simple equality: {field: value}
    Eq(String, MongoValue),
    /// Operator condition: {field: {$op: value}}
    Op(String, String, MongoValue),
    /// Range: {field: {$gte: v1, $lte: v2}}
    Range(String, MongoValue, MongoValue),
    /// Exists: {field: {$exists: bool}}
    Exists(String, bool),
    /// In/NotIn: {field: {$in/$nin: [values]}}
    InList(String, String, Vec<MongoValue>),
}

// Regex for BETWEEN parsing
static BETWEEN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)BETWEEN\s+(\S+)\s+AND\s+(\S+)").unwrap()
});

// SQL→MongoDB operator mapping
fn sql_to_mongo_op(op: &str) -> Option<&'static str> {
    match op {
        "=" => Some("$eq"),
        ">" => Some("$gt"),
        ">=" => Some("$gte"),
        "<" => Some("$lt"),
        "<=" => Some("$lte"),
        "<>" | "!=" => Some("$ne"),
        "IS" => Some("$exists"),
        "IS NOT" => Some("$exists"),
        _ => None,
    }
}

/// Valid MongoDB operators
fn is_mongo_operator(s: &str) -> bool {
    matches!(
        s,
        "$eq" | "$gt" | "$gte" | "$lt" | "$lte" | "$ne"
            | "$in" | "$nin" | "$exists" | "$type" | "$regex"
    )
}

/// Parse field_components-like logic: extract prefix, name, suffix
fn parse_field_key(key: &str) -> (String, String) {
    // Simple extraction: if key ends with '!', strip it
    if let Some(stripped) = key.strip_suffix('!') {
        (stripped.to_string(), "!".to_string())
    } else {
        (key.to_string(), String::new())
    }
}

/// Convert a value based on field_type hint (pure Rust, no GIL).
fn convert_value(value: &MongoValue, field_type: &Option<String>) -> MongoValue {
    match field_type.as_deref() {
        Some("string") => match value {
            MongoValue::Int(i) => MongoValue::Str(i.to_string()),
            MongoValue::Float(f) => MongoValue::Str(f.to_string()),
            MongoValue::Bool(b) => MongoValue::Str(b.to_string()),
            _ => value.clone(),
        },
        Some("integer") => match value {
            MongoValue::Str(s) => s.parse::<i64>().map_or(value.clone(), MongoValue::Int),
            _ => value.clone(),
        },
        Some("float") => match value {
            MongoValue::Str(s) => s.parse::<f64>().map_or(value.clone(), MongoValue::Float),
            _ => value.clone(),
        },
        Some("boolean") => match value {
            MongoValue::Str(s) => {
                let lower = s.to_lowercase();
                MongoValue::Bool(matches!(lower.as_str(), "true" | "yes" | "1"))
            }
            _ => value.clone(),
        },
        _ => value.clone(),
    }
}

// ---------------------------------------------------------------------------
// Phase 1: Extract from Python (serial, holds GIL)
// ---------------------------------------------------------------------------

fn extract_mongo_value(py_val: &Bound<'_, PyAny>) -> MongoValue {
    if py_val.is_none() {
        return MongoValue::None;
    }
    // Bool before int (Python bool is int subclass)
    if let Ok(b) = py_val.extract::<bool>() {
        return MongoValue::Bool(b);
    }
    if let Ok(i) = py_val.extract::<i64>() {
        return MongoValue::Int(i);
    }
    if let Ok(f) = py_val.extract::<f64>() {
        return MongoValue::Float(f);
    }
    if let Ok(s) = py_val.extract::<String>() {
        return MongoValue::Str(s);
    }
    // Dict: extract first key-value pair
    if let Ok(d) = py_val.downcast::<PyDict>() {
        if let Some((k, v)) = d.iter().next() {
            if let (Ok(op), val) = (k.extract::<String>(), extract_mongo_value(&v)) {
                return MongoValue::Dict {
                    op,
                    val: Box::new(val),
                };
            }
        }
        return MongoValue::None;
    }
    // List
    if let Ok(list) = py_val.downcast::<PyList>() {
        let items: Vec<MongoValue> = list.iter().map(|item| extract_mongo_value(&item)).collect();
        // Check if first item is a MongoDB operator string
        if let Some(MongoValue::Str(ref first)) = items.first() {
            if is_mongo_operator(first) {
                let op = first.clone();
                let val = if items.len() > 1 {
                    items[1].clone()
                } else {
                    MongoValue::None
                };
                return MongoValue::ListWithOp {
                    op,
                    val: Box::new(val),
                };
            }
        }
        return MongoValue::List(items);
    }
    MongoValue::None
}

fn extract_filter_entries(
    filter_dict: &Bound<'_, PyDict>,
    cond_definition: &Bound<'_, PyDict>,
) -> Vec<MongoFilterEntry> {
    filter_dict
        .iter()
        .filter_map(|(py_key, py_val)| {
            let key: String = py_key.extract().ok()?;
            let (field_name, suffix) = parse_field_key(&key);
            let field_type: Option<String> = cond_definition
                .get_item(&key)
                .ok()
                .flatten()
                .and_then(|v| v.extract().ok());
            let value = extract_mongo_value(&py_val);
            Some(MongoFilterEntry {
                _key: key,
                field_name,
                suffix,
                value,
                field_type,
            })
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Phase 2: Process entries in parallel (no GIL, pure Rust)
// ---------------------------------------------------------------------------

fn process_mongo_entry(entry: &MongoFilterEntry) -> Option<MongoCondition> {
    let ft = &entry.field_type;
    let field = &entry.field_name;

    match &entry.value {
        MongoValue::Dict { op, val } => {
            // SQL→Mongo operator conversion
            if let Some(mongo_op) = sql_to_mongo_op(op) {
                let converted = convert_value(val, ft);
                Some(MongoCondition::Op(
                    field.clone(),
                    mongo_op.to_string(),
                    converted,
                ))
            } else {
                // Already a MongoDB operator
                let converted = convert_value(val, ft);
                Some(MongoCondition::Op(field.clone(), op.clone(), converted))
            }
        }
        MongoValue::ListWithOp { op, val } => {
            let converted = convert_value(val, ft);
            Some(MongoCondition::Op(field.clone(), op.clone(), converted))
        }
        MongoValue::List(items) => {
            let converted: Vec<MongoValue> = items.iter().map(|v| convert_value(v, ft)).collect();
            if entry.suffix == "!" {
                Some(MongoCondition::InList(
                    field.clone(),
                    "$nin".to_string(),
                    converted,
                ))
            } else {
                Some(MongoCondition::InList(
                    field.clone(),
                    "$in".to_string(),
                    converted,
                ))
            }
        }
        MongoValue::Str(s) => {
            let upper = s.to_uppercase();
            if upper == "NULL" || upper == "NONE" {
                Some(MongoCondition::Exists(field.clone(), false))
            } else if upper == "!NULL" || upper == "!NONE" {
                Some(MongoCondition::Exists(field.clone(), true))
            } else if upper.contains("BETWEEN") {
                if let Some(caps) = BETWEEN_RE.captures(s) {
                    let low = MongoValue::Str(caps[1].to_string());
                    let high = MongoValue::Str(caps[2].to_string());
                    let low_c = convert_value(&low, ft);
                    let high_c = convert_value(&high, ft);
                    Some(MongoCondition::Range(field.clone(), low_c, high_c))
                } else {
                    None
                }
            } else if let Some(stripped) = s.strip_prefix('!') {
                let val = convert_value(&MongoValue::Str(stripped.to_string()), ft);
                Some(MongoCondition::Op(field.clone(), "$ne".to_string(), val))
            } else {
                let val = convert_value(&MongoValue::Str(s.clone()), ft);
                Some(MongoCondition::Eq(field.clone(), val))
            }
        }
        MongoValue::Bool(b) => Some(MongoCondition::Eq(field.clone(), MongoValue::Bool(*b))),
        MongoValue::Int(i) => {
            let val = convert_value(&MongoValue::Int(*i), ft);
            Some(MongoCondition::Eq(field.clone(), val))
        }
        MongoValue::Float(f) => {
            let val = convert_value(&MongoValue::Float(*f), ft);
            Some(MongoCondition::Eq(field.clone(), val))
        }
        MongoValue::None => Some(MongoCondition::Exists(field.clone(), false)),
    }
}

// ---------------------------------------------------------------------------
// Phase 3: Convert back to Python dict (serial, holds GIL)
// ---------------------------------------------------------------------------

fn mongo_value_to_py(py: Python, val: &MongoValue) -> PyObject {
    match val {
        MongoValue::Str(s) => s.into_pyobject(py).unwrap().into_any().unbind(),
        MongoValue::Int(i) => i.into_pyobject(py).unwrap().into_any().unbind(),
        MongoValue::Float(f) => f.into_pyobject(py).unwrap().into_any().unbind(),
        #[allow(deprecated)]
        MongoValue::Bool(b) => b.to_object(py),
        MongoValue::None => py.None(),
        MongoValue::List(items) => {
            let list = PyList::new(py, items.iter().map(|v| mongo_value_to_py(py, v))).unwrap();
            list.into_pyobject(py).unwrap().into_any().unbind()
        }
        MongoValue::Dict { op, val } => {
            let d = PyDict::new(py);
            d.set_item(op, mongo_value_to_py(py, val)).unwrap();
            d.into_pyobject(py).unwrap().into_any().unbind()
        }
        MongoValue::ListWithOp { op, val } => {
            let d = PyDict::new(py);
            d.set_item(op, mongo_value_to_py(py, val)).unwrap();
            d.into_pyobject(py).unwrap().into_any().unbind()
        }
    }
}

fn condition_to_py(py: Python, cond: &MongoCondition, result: &Bound<'_, PyDict>) {
    match cond {
        MongoCondition::Eq(field, val) => {
            result.set_item(field, mongo_value_to_py(py, val)).unwrap();
        }
        MongoCondition::Op(field, op, val) => {
            let inner = PyDict::new(py);
            inner.set_item(op, mongo_value_to_py(py, val)).unwrap();
            result.set_item(field, inner).unwrap();
        }
        MongoCondition::Range(field, low, high) => {
            let inner = PyDict::new(py);
            inner
                .set_item("$gte", mongo_value_to_py(py, low))
                .unwrap();
            inner
                .set_item("$lte", mongo_value_to_py(py, high))
                .unwrap();
            result.set_item(field, inner).unwrap();
        }
        MongoCondition::Exists(field, exists) => {
            let inner = PyDict::new(py);
            inner.set_item("$exists", *exists).unwrap();
            result.set_item(field, inner).unwrap();
        }
        MongoCondition::InList(field, op, items) => {
            let py_items: Vec<PyObject> = items.iter().map(|v| mongo_value_to_py(py, v)).collect();
            let list = PyList::new(py, &py_items).unwrap();
            let inner = PyDict::new(py);
            inner.set_item(op, list).unwrap();
            result.set_item(field, inner).unwrap();
        }
    }
}

// ---------------------------------------------------------------------------
// Public PyO3 functions
// ---------------------------------------------------------------------------

/// Build MongoDB filter conditions dict with rayon parallel processing.
///
/// 1. Extract filter entries from Python (serial, GIL)
/// 2. Process each entry in parallel via rayon (no GIL)
/// 3. Build result Python dict (serial, GIL)
#[pyfunction]
#[pyo3(signature = (filter_dict, cond_definition))]
pub fn mongo_filter_conditions(
    py: Python,
    filter_dict: &Bound<'_, PyDict>,
    cond_definition: &Bound<'_, PyDict>,
) -> PyResult<PyObject> {
    let result = PyDict::new(py);

    if filter_dict.is_empty() {
        return Ok(result.into_pyobject(py).unwrap().into_any().unbind());
    }

    // Phase 1: Extract (serial, GIL)
    let entries = extract_filter_entries(filter_dict, cond_definition);

    // Phase 2: Process in parallel (no GIL)
    let conditions: Vec<MongoCondition> = entries
        .par_iter()
        .filter_map(|e| process_mongo_entry(e))
        .collect();

    // Phase 3: Build Python dict (serial, GIL)
    for cond in &conditions {
        condition_to_py(py, cond, &result);
    }

    Ok(result.into_pyobject(py).unwrap().into_any().unbind())
}

/// Build MongoDB projection dict from a list of field names.
///
/// Returns {field: 1, ...} with _id: 0 exclusion by default.
/// Returns None if no fields.
#[pyfunction]
#[pyo3(signature = (fields))]
pub fn mongo_process_fields(py: Python, fields: Vec<String>) -> PyResult<PyObject> {
    if fields.is_empty() {
        return Ok(py.None());
    }

    let result = PyDict::new(py);
    for field in &fields {
        result.set_item(field, 1)?;
    }

    // Exclude _id unless explicitly included
    if !fields.iter().any(|f| f == "_id") {
        result.set_item("_id", 0)?;
    }

    Ok(result.into_pyobject(py).unwrap().into_any().unbind())
}

/// Build MongoDB sort specification from ordering.
///
/// Input: list of strings like ["name", "-created_at"]
/// Output: list of tuples [("name", 1), ("created_at", -1)]
/// Returns None if empty.
#[pyfunction]
#[pyo3(signature = (ordering))]
pub fn mongo_process_ordering(py: Python, ordering: Vec<String>) -> PyResult<PyObject> {
    if ordering.is_empty() {
        return Ok(py.None());
    }

    // Process in parallel for large orderings
    let sort_items: Vec<(String, i32)> = ordering
        .par_iter()
        .map(|item| {
            let trimmed = item.trim();
            if let Some(field) = trimmed.strip_prefix('-') {
                (field.to_string(), -1)
            } else {
                (trimmed.to_string(), 1)
            }
        })
        .collect();

    let result = PyList::empty(py);
    for (field, direction) in &sort_items {
        let tuple = PyTuple::new(py, &[
            field.into_pyobject(py).unwrap().into_any().unbind(),
            direction.into_pyobject(py).unwrap().into_any().unbind(),
        ])?;
        result.append(tuple)?;
    }

    Ok(result.into_pyobject(py).unwrap().into_any().unbind())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_field_key_normal() {
        let (name, suffix) = parse_field_key("status");
        assert_eq!(name, "status");
        assert_eq!(suffix, "");
    }

    #[test]
    fn test_parse_field_key_negation() {
        let (name, suffix) = parse_field_key("status!");
        assert_eq!(name, "status");
        assert_eq!(suffix, "!");
    }

    #[test]
    fn test_sql_to_mongo_operators() {
        assert_eq!(sql_to_mongo_op("="), Some("$eq"));
        assert_eq!(sql_to_mongo_op(">"), Some("$gt"));
        assert_eq!(sql_to_mongo_op(">="), Some("$gte"));
        assert_eq!(sql_to_mongo_op("<"), Some("$lt"));
        assert_eq!(sql_to_mongo_op("<="), Some("$lte"));
        assert_eq!(sql_to_mongo_op("<>"), Some("$ne"));
        assert_eq!(sql_to_mongo_op("!="), Some("$ne"));
        assert_eq!(sql_to_mongo_op("IS"), Some("$exists"));
        assert_eq!(sql_to_mongo_op("unknown"), None);
    }

    #[test]
    fn test_is_mongo_operator() {
        assert!(is_mongo_operator("$eq"));
        assert!(is_mongo_operator("$in"));
        assert!(is_mongo_operator("$regex"));
        assert!(!is_mongo_operator("eq"));
        assert!(!is_mongo_operator("IN"));
    }

    #[test]
    fn test_process_null_entry() {
        let entry = MongoFilterEntry {
            key: "email".to_string(),
            field_name: "email".to_string(),
            suffix: String::new(),
            value: MongoValue::Str("null".to_string()),
            field_type: None,
        };
        let result = process_mongo_entry(&entry).unwrap();
        match result {
            MongoCondition::Exists(field, exists) => {
                assert_eq!(field, "email");
                assert!(!exists);
            }
            _ => panic!("Expected Exists condition"),
        }
    }

    #[test]
    fn test_process_not_null_entry() {
        let entry = MongoFilterEntry {
            key: "email".to_string(),
            field_name: "email".to_string(),
            suffix: String::new(),
            value: MongoValue::Str("!null".to_string()),
            field_type: None,
        };
        let result = process_mongo_entry(&entry).unwrap();
        match result {
            MongoCondition::Exists(field, exists) => {
                assert_eq!(field, "email");
                assert!(exists);
            }
            _ => panic!("Expected Exists condition"),
        }
    }

    #[test]
    fn test_process_negation_entry() {
        let entry = MongoFilterEntry {
            key: "status".to_string(),
            field_name: "status".to_string(),
            suffix: String::new(),
            value: MongoValue::Str("!active".to_string()),
            field_type: None,
        };
        let result = process_mongo_entry(&entry).unwrap();
        match result {
            MongoCondition::Op(field, op, _) => {
                assert_eq!(field, "status");
                assert_eq!(op, "$ne");
            }
            _ => panic!("Expected Op condition"),
        }
    }

    #[test]
    fn test_process_equality_entry() {
        let entry = MongoFilterEntry {
            key: "name".to_string(),
            field_name: "name".to_string(),
            suffix: String::new(),
            value: MongoValue::Str("admin".to_string()),
            field_type: None,
        };
        let result = process_mongo_entry(&entry).unwrap();
        match result {
            MongoCondition::Eq(field, MongoValue::Str(val)) => {
                assert_eq!(field, "name");
                assert_eq!(val, "admin");
            }
            _ => panic!("Expected Eq condition"),
        }
    }

    #[test]
    fn test_process_int_entry() {
        let entry = MongoFilterEntry {
            key: "age".to_string(),
            field_name: "age".to_string(),
            suffix: String::new(),
            value: MongoValue::Int(30),
            field_type: None,
        };
        let result = process_mongo_entry(&entry).unwrap();
        match result {
            MongoCondition::Eq(field, MongoValue::Int(v)) => {
                assert_eq!(field, "age");
                assert_eq!(v, 30);
            }
            _ => panic!("Expected Eq condition"),
        }
    }

    #[test]
    fn test_process_bool_entry() {
        let entry = MongoFilterEntry {
            key: "active".to_string(),
            field_name: "active".to_string(),
            suffix: String::new(),
            value: MongoValue::Bool(true),
            field_type: None,
        };
        let result = process_mongo_entry(&entry).unwrap();
        match result {
            MongoCondition::Eq(field, MongoValue::Bool(v)) => {
                assert_eq!(field, "active");
                assert!(v);
            }
            _ => panic!("Expected Eq condition"),
        }
    }

    #[test]
    fn test_process_none_entry() {
        let entry = MongoFilterEntry {
            key: "deleted".to_string(),
            field_name: "deleted".to_string(),
            suffix: String::new(),
            value: MongoValue::None,
            field_type: None,
        };
        let result = process_mongo_entry(&entry).unwrap();
        match result {
            MongoCondition::Exists(field, exists) => {
                assert_eq!(field, "deleted");
                assert!(!exists);
            }
            _ => panic!("Expected Exists condition"),
        }
    }

    #[test]
    fn test_process_in_list() {
        let entry = MongoFilterEntry {
            key: "status".to_string(),
            field_name: "status".to_string(),
            suffix: String::new(),
            value: MongoValue::List(vec![
                MongoValue::Str("active".to_string()),
                MongoValue::Str("pending".to_string()),
            ]),
            field_type: None,
        };
        let result = process_mongo_entry(&entry).unwrap();
        match result {
            MongoCondition::InList(field, op, items) => {
                assert_eq!(field, "status");
                assert_eq!(op, "$in");
                assert_eq!(items.len(), 2);
            }
            _ => panic!("Expected InList condition"),
        }
    }

    #[test]
    fn test_process_nin_list() {
        let entry = MongoFilterEntry {
            key: "status!".to_string(),
            field_name: "status".to_string(),
            suffix: "!".to_string(),
            value: MongoValue::List(vec![
                MongoValue::Str("deleted".to_string()),
            ]),
            field_type: None,
        };
        let result = process_mongo_entry(&entry).unwrap();
        match result {
            MongoCondition::InList(field, op, _) => {
                assert_eq!(field, "status");
                assert_eq!(op, "$nin");
            }
            _ => panic!("Expected InList condition"),
        }
    }

    #[test]
    fn test_process_dict_operator() {
        let entry = MongoFilterEntry {
            key: "age".to_string(),
            field_name: "age".to_string(),
            suffix: String::new(),
            value: MongoValue::Dict {
                op: ">=".to_string(),
                val: Box::new(MongoValue::Int(18)),
            },
            field_type: None,
        };
        let result = process_mongo_entry(&entry).unwrap();
        match result {
            MongoCondition::Op(field, op, MongoValue::Int(v)) => {
                assert_eq!(field, "age");
                assert_eq!(op, "$gte");
                assert_eq!(v, 18);
            }
            _ => panic!("Expected Op condition"),
        }
    }

    #[test]
    fn test_process_between() {
        let entry = MongoFilterEntry {
            key: "created_at".to_string(),
            field_name: "created_at".to_string(),
            suffix: String::new(),
            value: MongoValue::Str("BETWEEN 2024-01-01 AND 2024-12-31".to_string()),
            field_type: None,
        };
        let result = process_mongo_entry(&entry).unwrap();
        match result {
            MongoCondition::Range(field, MongoValue::Str(low), MongoValue::Str(high)) => {
                assert_eq!(field, "created_at");
                assert_eq!(low, "2024-01-01");
                assert_eq!(high, "2024-12-31");
            }
            _ => panic!("Expected Range condition"),
        }
    }

    #[test]
    fn test_convert_value_to_int() {
        let val = MongoValue::Str("42".to_string());
        let ft = Some("integer".to_string());
        match convert_value(&val, &ft) {
            MongoValue::Int(i) => assert_eq!(i, 42),
            _ => panic!("Expected Int"),
        }
    }

    #[test]
    fn test_convert_value_to_float() {
        let val = MongoValue::Str("3.14".to_string());
        let ft = Some("float".to_string());
        match convert_value(&val, &ft) {
            MongoValue::Float(f) => assert!((f - 3.14).abs() < 0.001),
            _ => panic!("Expected Float"),
        }
    }

    #[test]
    fn test_convert_value_to_bool() {
        let val = MongoValue::Str("true".to_string());
        let ft = Some("boolean".to_string());
        match convert_value(&val, &ft) {
            MongoValue::Bool(b) => assert!(b),
            _ => panic!("Expected Bool"),
        }
    }

    #[test]
    fn test_list_with_mongo_op() {
        let entry = MongoFilterEntry {
            key: "score".to_string(),
            field_name: "score".to_string(),
            suffix: String::new(),
            value: MongoValue::ListWithOp {
                op: "$gte".to_string(),
                val: Box::new(MongoValue::Int(90)),
            },
            field_type: None,
        };
        let result = process_mongo_entry(&entry).unwrap();
        match result {
            MongoCondition::Op(field, op, MongoValue::Int(v)) => {
                assert_eq!(field, "score");
                assert_eq!(op, "$gte");
                assert_eq!(v, 90);
            }
            _ => panic!("Expected Op condition"),
        }
    }
}
