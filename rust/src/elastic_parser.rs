// Copyright (C) 2018-present Jesus Lara
//
// elastic_parser.rs — Elasticsearch Query DSL builder with rayon parallelism.
// Builds Elasticsearch Query DSL dicts from QuerySource filter conditions.
// Follows the 3-phase pattern: extract from Python → parallel process → convert back.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rayon::prelude::*;
use regex::Regex;
use once_cell::sync::Lazy;

// ---------------------------------------------------------------------------
// Shared types for parallel processing
// ---------------------------------------------------------------------------

/// Represents a single filter entry extracted from Python (GIL-free).
#[derive(Debug, Clone)]
struct EsFilterEntry {
    _key: String,
    field_name: String,
    suffix: String,
    value: EsValue,
    field_type: Option<String>,
}

/// Represents the possible value types from Python filter dict.
#[derive(Debug, Clone)]
enum EsValue {
    Str(String),
    Int(i64),
    Float(f64),
    Bool(bool),
    None,
    Dict { op: String, val: Box<EsValue> },
    List(Vec<EsValue>),
    ListWithOp { op: String, val: Box<EsValue> },
}

/// Represents a processed Elasticsearch condition (GIL-free).
#[derive(Debug, Clone)]
enum EsCondition {
    /// Term query: {"term": {"field": value}}
    Term(String, EsValue),
    /// Range query: {"range": {"field": {"gt"/"gte"/etc: value}}}
    Range(String, String, EsValue),
    /// Range between: {"range": {"field": {"gte": v1, "lte": v2}}}
    RangeBetween(String, EsValue, EsValue),
    /// Exists: {"exists": {"field": "field"}} or must_not exists
    Exists(String, bool),
    /// Terms (IN): {"terms": {"field": [values]}}
    Terms(String, Vec<EsValue>),
    /// Must not terms (NOT IN): must_not {"terms": {"field": [values]}}
    MustNotTerms(String, Vec<EsValue>),
    /// Must not term (!=): must_not {"term": {"field": value}}
    MustNotTerm(String, EsValue),
}

// Regex for BETWEEN parsing
static BETWEEN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)BETWEEN\s+(\S+)\s+AND\s+(\S+)").unwrap()
});

// SQL operator to ES range operator mapping
fn sql_to_es_range_op(op: &str) -> Option<&'static str> {
    match op {
        ">" => Some("gt"),
        ">=" => Some("gte"),
        "<" => Some("lt"),
        "<=" => Some("lte"),
        _ => None,
    }
}

/// Parse field key: extract name and suffix (e.g., "status!" → ("status", "!"))
fn parse_field_key(key: &str) -> (String, String) {
    if let Some(stripped) = key.strip_suffix('!') {
        (stripped.to_string(), "!".to_string())
    } else {
        (key.to_string(), String::new())
    }
}

/// Convert a value based on field_type hint (pure Rust, no GIL).
fn convert_value(value: &EsValue, field_type: &Option<String>) -> EsValue {
    match field_type.as_deref() {
        Some("string") => match value {
            EsValue::Int(i) => EsValue::Str(i.to_string()),
            EsValue::Float(f) => EsValue::Str(f.to_string()),
            EsValue::Bool(b) => EsValue::Str(b.to_string()),
            _ => value.clone(),
        },
        Some("integer") => match value {
            EsValue::Str(s) => s.parse::<i64>().map_or(value.clone(), EsValue::Int),
            _ => value.clone(),
        },
        Some("float") => match value {
            EsValue::Str(s) => s.parse::<f64>().map_or(value.clone(), EsValue::Float),
            _ => value.clone(),
        },
        Some("boolean") => match value {
            EsValue::Str(s) => {
                let lower = s.to_lowercase();
                EsValue::Bool(matches!(lower.as_str(), "true" | "yes" | "1"))
            }
            _ => value.clone(),
        },
        _ => value.clone(),
    }
}

// ---------------------------------------------------------------------------
// Phase 1: Extract from Python (serial, holds GIL)
// ---------------------------------------------------------------------------

fn extract_es_value(py_val: &Bound<'_, PyAny>) -> EsValue {
    if py_val.is_none() {
        return EsValue::None;
    }
    // Bool before int (Python bool is int subclass)
    if let Ok(b) = py_val.extract::<bool>() {
        return EsValue::Bool(b);
    }
    if let Ok(i) = py_val.extract::<i64>() {
        return EsValue::Int(i);
    }
    if let Ok(f) = py_val.extract::<f64>() {
        return EsValue::Float(f);
    }
    if let Ok(s) = py_val.extract::<String>() {
        return EsValue::Str(s);
    }
    // Dict: extract first key-value pair
    if let Ok(d) = py_val.downcast::<PyDict>() {
        if let Some((k, v)) = d.iter().next() {
            if let (Ok(op), val) = (k.extract::<String>(), extract_es_value(&v)) {
                return EsValue::Dict {
                    op,
                    val: Box::new(val),
                };
            }
        }
        return EsValue::None;
    }
    // List
    if let Ok(list) = py_val.downcast::<PyList>() {
        let items: Vec<EsValue> = list.iter().map(|item| extract_es_value(&item)).collect();
        // Check if first item is an operator string (e.g., [">", 10])
        if let Some(EsValue::Str(ref first)) = items.first() {
            if sql_to_es_range_op(first).is_some()
                || first == "="
                || first == "!="
                || first == "<>"
            {
                let op = first.clone();
                let val = if items.len() > 1 {
                    items[1].clone()
                } else {
                    EsValue::None
                };
                return EsValue::ListWithOp {
                    op,
                    val: Box::new(val),
                };
            }
        }
        return EsValue::List(items);
    }
    EsValue::None
}

fn extract_filter_entries(
    filter_dict: &Bound<'_, PyDict>,
    cond_definition: &Bound<'_, PyDict>,
) -> Vec<EsFilterEntry> {
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
            let value = extract_es_value(&py_val);
            Some(EsFilterEntry {
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

fn process_es_entry(entry: &EsFilterEntry) -> Option<EsCondition> {
    let ft = &entry.field_type;
    let field = &entry.field_name;

    match &entry.value {
        EsValue::Dict { op, val } => {
            // Check if it's a range operator
            if let Some(es_op) = sql_to_es_range_op(op) {
                let converted = convert_value(val, ft);
                Some(EsCondition::Range(
                    field.clone(),
                    es_op.to_string(),
                    converted,
                ))
            } else if op == "=" {
                let converted = convert_value(val, ft);
                Some(EsCondition::Term(field.clone(), converted))
            } else if op == "!=" || op == "<>" {
                let converted = convert_value(val, ft);
                Some(EsCondition::MustNotTerm(field.clone(), converted))
            } else if op == "IS" {
                Some(EsCondition::Exists(field.clone(), true))
            } else if op == "IS NOT" {
                Some(EsCondition::Exists(field.clone(), false))
            } else {
                // Unknown operator, treat as term
                let converted = convert_value(val, ft);
                Some(EsCondition::Term(field.clone(), converted))
            }
        }
        EsValue::ListWithOp { op, val } => {
            if let Some(es_op) = sql_to_es_range_op(op) {
                let converted = convert_value(val, ft);
                Some(EsCondition::Range(
                    field.clone(),
                    es_op.to_string(),
                    converted,
                ))
            } else if op == "=" {
                let converted = convert_value(val, ft);
                Some(EsCondition::Term(field.clone(), converted))
            } else if op == "!=" || op == "<>" {
                let converted = convert_value(val, ft);
                Some(EsCondition::MustNotTerm(field.clone(), converted))
            } else {
                let converted = convert_value(val, ft);
                Some(EsCondition::Term(field.clone(), converted))
            }
        }
        EsValue::List(items) => {
            let converted: Vec<EsValue> = items.iter().map(|v| convert_value(v, ft)).collect();
            if entry.suffix == "!" {
                Some(EsCondition::MustNotTerms(field.clone(), converted))
            } else {
                Some(EsCondition::Terms(field.clone(), converted))
            }
        }
        EsValue::Str(s) => {
            let upper = s.to_uppercase();
            if upper == "NULL" || upper == "NONE" {
                Some(EsCondition::Exists(field.clone(), false))
            } else if upper == "!NULL" || upper == "!NONE" {
                Some(EsCondition::Exists(field.clone(), true))
            } else if upper.contains("BETWEEN") {
                if let Some(caps) = BETWEEN_RE.captures(s) {
                    let low = EsValue::Str(caps[1].to_string());
                    let high = EsValue::Str(caps[2].to_string());
                    let low_c = convert_value(&low, ft);
                    let high_c = convert_value(&high, ft);
                    Some(EsCondition::RangeBetween(field.clone(), low_c, high_c))
                } else {
                    None
                }
            } else if let Some(stripped) = s.strip_prefix('!') {
                let val = convert_value(&EsValue::Str(stripped.to_string()), ft);
                Some(EsCondition::MustNotTerm(field.clone(), val))
            } else {
                let val = convert_value(&EsValue::Str(s.clone()), ft);
                Some(EsCondition::Term(field.clone(), val))
            }
        }
        EsValue::Bool(b) => Some(EsCondition::Term(field.clone(), EsValue::Bool(*b))),
        EsValue::Int(i) => {
            let val = convert_value(&EsValue::Int(*i), ft);
            Some(EsCondition::Term(field.clone(), val))
        }
        EsValue::Float(f) => {
            let val = convert_value(&EsValue::Float(*f), ft);
            Some(EsCondition::Term(field.clone(), val))
        }
        EsValue::None => Some(EsCondition::Exists(field.clone(), false)),
    }
}

// ---------------------------------------------------------------------------
// Phase 3: Convert back to Python dicts (serial, holds GIL)
// ---------------------------------------------------------------------------

fn es_value_to_py(py: Python, val: &EsValue) -> PyObject {
    match val {
        EsValue::Str(s) => s.into_pyobject(py).unwrap().into_any().unbind(),
        EsValue::Int(i) => i.into_pyobject(py).unwrap().into_any().unbind(),
        EsValue::Float(f) => f.into_pyobject(py).unwrap().into_any().unbind(),
        #[allow(deprecated)]
        EsValue::Bool(b) => b.to_object(py),
        EsValue::None => py.None(),
        EsValue::List(items) => {
            let list = PyList::new(py, items.iter().map(|v| es_value_to_py(py, v))).unwrap();
            list.into_pyobject(py).unwrap().into_any().unbind()
        }
        EsValue::Dict { op, val } => {
            let d = PyDict::new(py);
            d.set_item(op, es_value_to_py(py, val)).unwrap();
            d.into_pyobject(py).unwrap().into_any().unbind()
        }
        EsValue::ListWithOp { op, val } => {
            let d = PyDict::new(py);
            d.set_item(op, es_value_to_py(py, val)).unwrap();
            d.into_pyobject(py).unwrap().into_any().unbind()
        }
    }
}

/// Build an ES bool query dict from conditions.
///
/// Returns: {"must": [...], "must_not": [...], "filter": [...]}
fn conditions_to_py(py: Python, conditions: &[EsCondition]) -> PyObject {
    let must = PyList::empty(py);
    let must_not = PyList::empty(py);
    let filter = PyList::empty(py);

    for cond in conditions {
        match cond {
            EsCondition::Term(field, val) => {
                let clause = PyDict::new(py);
                let inner = PyDict::new(py);
                inner.set_item(field.as_str(), es_value_to_py(py, val)).unwrap();
                clause.set_item("term", inner).unwrap();
                filter.append(clause).unwrap();
            }
            EsCondition::Range(field, op, val) => {
                let clause = PyDict::new(py);
                let field_dict = PyDict::new(py);
                let range_dict = PyDict::new(py);
                range_dict.set_item(op.as_str(), es_value_to_py(py, val)).unwrap();
                field_dict.set_item(field.as_str(), range_dict).unwrap();
                clause.set_item("range", field_dict).unwrap();
                filter.append(clause).unwrap();
            }
            EsCondition::RangeBetween(field, low, high) => {
                let clause = PyDict::new(py);
                let field_dict = PyDict::new(py);
                let range_dict = PyDict::new(py);
                range_dict.set_item("gte", es_value_to_py(py, low)).unwrap();
                range_dict.set_item("lte", es_value_to_py(py, high)).unwrap();
                field_dict.set_item(field.as_str(), range_dict).unwrap();
                clause.set_item("range", field_dict).unwrap();
                filter.append(clause).unwrap();
            }
            EsCondition::Exists(field, exists) => {
                let clause = PyDict::new(py);
                let inner = PyDict::new(py);
                inner.set_item("field", field.as_str()).unwrap();
                clause.set_item("exists", inner).unwrap();
                if *exists {
                    filter.append(clause).unwrap();
                } else {
                    must_not.append(clause).unwrap();
                }
            }
            EsCondition::Terms(field, items) => {
                let clause = PyDict::new(py);
                let inner = PyDict::new(py);
                let py_items: Vec<PyObject> =
                    items.iter().map(|v| es_value_to_py(py, v)).collect();
                let list = PyList::new(py, &py_items).unwrap();
                inner.set_item(field.as_str(), list).unwrap();
                clause.set_item("terms", inner).unwrap();
                filter.append(clause).unwrap();
            }
            EsCondition::MustNotTerms(field, items) => {
                let clause = PyDict::new(py);
                let inner = PyDict::new(py);
                let py_items: Vec<PyObject> =
                    items.iter().map(|v| es_value_to_py(py, v)).collect();
                let list = PyList::new(py, &py_items).unwrap();
                inner.set_item(field.as_str(), list).unwrap();
                clause.set_item("terms", inner).unwrap();
                must_not.append(clause).unwrap();
            }
            EsCondition::MustNotTerm(field, val) => {
                let clause = PyDict::new(py);
                let inner = PyDict::new(py);
                inner.set_item(field.as_str(), es_value_to_py(py, val)).unwrap();
                clause.set_item("term", inner).unwrap();
                must_not.append(clause).unwrap();
            }
        }
    }

    let result = PyDict::new(py);
    if !must.is_empty() {
        result.set_item("must", must).unwrap();
    }
    if !must_not.is_empty() {
        result.set_item("must_not", must_not).unwrap();
    }
    if !filter.is_empty() {
        result.set_item("filter", filter).unwrap();
    }
    result.into_pyobject(py).unwrap().into_any().unbind()
}

// ---------------------------------------------------------------------------
// Public PyO3 functions
// ---------------------------------------------------------------------------

/// Build Elasticsearch bool query from filter conditions.
///
/// 1. Extract filter entries from Python (serial, GIL)
/// 2. Process each entry in parallel via rayon (no GIL)
/// 3. Build result Python dict (serial, GIL)
///
/// Returns: {"must": [...], "must_not": [...], "filter": [...]}
#[pyfunction]
#[pyo3(signature = (filter_dict, cond_definition))]
pub fn es_filter_conditions(
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
    let conditions: Vec<EsCondition> = entries
        .par_iter()
        .filter_map(|e| process_es_entry(e))
        .collect();

    // Phase 3: Build Python dict (serial, GIL)
    Ok(conditions_to_py(py, &conditions))
}

/// Build Elasticsearch _source list from field names.
///
/// Returns list of field names for _source filtering, or None if empty.
#[pyfunction]
#[pyo3(signature = (fields))]
pub fn es_process_fields(py: Python, fields: Vec<String>) -> PyResult<PyObject> {
    if fields.is_empty() {
        return Ok(py.None());
    }

    let result = PyList::new(py, &fields)?;
    Ok(result.into_pyobject(py).unwrap().into_any().unbind())
}

/// Build Elasticsearch sort specification from ordering.
///
/// Input: list of strings like ["name", "-created_at"]
/// Output: list of dicts [{"name": "asc"}, {"created_at": "desc"}]
/// Returns None if empty.
#[pyfunction]
#[pyo3(signature = (ordering))]
pub fn es_process_ordering(py: Python, ordering: Vec<String>) -> PyResult<PyObject> {
    if ordering.is_empty() {
        return Ok(py.None());
    }

    // Process in parallel for large orderings
    let sort_items: Vec<(String, &str)> = ordering
        .par_iter()
        .map(|item| {
            let trimmed = item.trim();
            if let Some(field) = trimmed.strip_prefix('-') {
                (field.to_string(), "desc")
            } else {
                (trimmed.to_string(), "asc")
            }
        })
        .collect();

    let result = PyList::empty(py);
    for (field, direction) in &sort_items {
        let entry = PyDict::new(py);
        entry.set_item(field.as_str(), *direction)?;
        result.append(entry)?;
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
    fn test_sql_to_es_range_operators() {
        assert_eq!(sql_to_es_range_op(">"), Some("gt"));
        assert_eq!(sql_to_es_range_op(">="), Some("gte"));
        assert_eq!(sql_to_es_range_op("<"), Some("lt"));
        assert_eq!(sql_to_es_range_op("<="), Some("lte"));
        assert_eq!(sql_to_es_range_op("="), None);
        assert_eq!(sql_to_es_range_op("unknown"), None);
    }

    #[test]
    fn test_process_null_entry() {
        let entry = EsFilterEntry {
            _key: "email".to_string(),
            field_name: "email".to_string(),
            suffix: String::new(),
            value: EsValue::Str("null".to_string()),
            field_type: None,
        };
        let result = process_es_entry(&entry).unwrap();
        match result {
            EsCondition::Exists(field, exists) => {
                assert_eq!(field, "email");
                assert!(!exists);
            }
            _ => panic!("Expected Exists(false) condition"),
        }
    }

    #[test]
    fn test_process_not_null_entry() {
        let entry = EsFilterEntry {
            _key: "email".to_string(),
            field_name: "email".to_string(),
            suffix: String::new(),
            value: EsValue::Str("!null".to_string()),
            field_type: None,
        };
        let result = process_es_entry(&entry).unwrap();
        match result {
            EsCondition::Exists(field, exists) => {
                assert_eq!(field, "email");
                assert!(exists);
            }
            _ => panic!("Expected Exists(true) condition"),
        }
    }

    #[test]
    fn test_process_negation_entry() {
        let entry = EsFilterEntry {
            _key: "status".to_string(),
            field_name: "status".to_string(),
            suffix: String::new(),
            value: EsValue::Str("!active".to_string()),
            field_type: None,
        };
        let result = process_es_entry(&entry).unwrap();
        match result {
            EsCondition::MustNotTerm(field, _) => {
                assert_eq!(field, "status");
            }
            _ => panic!("Expected MustNotTerm condition"),
        }
    }

    #[test]
    fn test_process_term_entry() {
        let entry = EsFilterEntry {
            _key: "name".to_string(),
            field_name: "name".to_string(),
            suffix: String::new(),
            value: EsValue::Str("admin".to_string()),
            field_type: None,
        };
        let result = process_es_entry(&entry).unwrap();
        match result {
            EsCondition::Term(field, EsValue::Str(val)) => {
                assert_eq!(field, "name");
                assert_eq!(val, "admin");
            }
            _ => panic!("Expected Term condition"),
        }
    }

    #[test]
    fn test_process_int_entry() {
        let entry = EsFilterEntry {
            _key: "age".to_string(),
            field_name: "age".to_string(),
            suffix: String::new(),
            value: EsValue::Int(30),
            field_type: None,
        };
        let result = process_es_entry(&entry).unwrap();
        match result {
            EsCondition::Term(field, EsValue::Int(v)) => {
                assert_eq!(field, "age");
                assert_eq!(v, 30);
            }
            _ => panic!("Expected Term condition"),
        }
    }

    #[test]
    fn test_process_bool_entry() {
        let entry = EsFilterEntry {
            _key: "active".to_string(),
            field_name: "active".to_string(),
            suffix: String::new(),
            value: EsValue::Bool(true),
            field_type: None,
        };
        let result = process_es_entry(&entry).unwrap();
        match result {
            EsCondition::Term(field, EsValue::Bool(v)) => {
                assert_eq!(field, "active");
                assert!(v);
            }
            _ => panic!("Expected Term condition"),
        }
    }

    #[test]
    fn test_process_none_entry() {
        let entry = EsFilterEntry {
            _key: "deleted".to_string(),
            field_name: "deleted".to_string(),
            suffix: String::new(),
            value: EsValue::None,
            field_type: None,
        };
        let result = process_es_entry(&entry).unwrap();
        match result {
            EsCondition::Exists(field, exists) => {
                assert_eq!(field, "deleted");
                assert!(!exists);
            }
            _ => panic!("Expected Exists condition"),
        }
    }

    #[test]
    fn test_process_in_list() {
        let entry = EsFilterEntry {
            _key: "status".to_string(),
            field_name: "status".to_string(),
            suffix: String::new(),
            value: EsValue::List(vec![
                EsValue::Str("active".to_string()),
                EsValue::Str("pending".to_string()),
            ]),
            field_type: None,
        };
        let result = process_es_entry(&entry).unwrap();
        match result {
            EsCondition::Terms(field, items) => {
                assert_eq!(field, "status");
                assert_eq!(items.len(), 2);
            }
            _ => panic!("Expected Terms condition"),
        }
    }

    #[test]
    fn test_process_not_in_list() {
        let entry = EsFilterEntry {
            _key: "status!".to_string(),
            field_name: "status".to_string(),
            suffix: "!".to_string(),
            value: EsValue::List(vec![EsValue::Str("deleted".to_string())]),
            field_type: None,
        };
        let result = process_es_entry(&entry).unwrap();
        match result {
            EsCondition::MustNotTerms(field, _) => {
                assert_eq!(field, "status");
            }
            _ => panic!("Expected MustNotTerms condition"),
        }
    }

    #[test]
    fn test_process_range_operator() {
        let entry = EsFilterEntry {
            _key: "age".to_string(),
            field_name: "age".to_string(),
            suffix: String::new(),
            value: EsValue::Dict {
                op: ">=".to_string(),
                val: Box::new(EsValue::Int(18)),
            },
            field_type: None,
        };
        let result = process_es_entry(&entry).unwrap();
        match result {
            EsCondition::Range(field, op, EsValue::Int(v)) => {
                assert_eq!(field, "age");
                assert_eq!(op, "gte");
                assert_eq!(v, 18);
            }
            _ => panic!("Expected Range condition"),
        }
    }

    #[test]
    fn test_process_between() {
        let entry = EsFilterEntry {
            _key: "created_at".to_string(),
            field_name: "created_at".to_string(),
            suffix: String::new(),
            value: EsValue::Str("BETWEEN 2024-01-01 AND 2024-12-31".to_string()),
            field_type: None,
        };
        let result = process_es_entry(&entry).unwrap();
        match result {
            EsCondition::RangeBetween(field, EsValue::Str(low), EsValue::Str(high)) => {
                assert_eq!(field, "created_at");
                assert_eq!(low, "2024-01-01");
                assert_eq!(high, "2024-12-31");
            }
            _ => panic!("Expected RangeBetween condition"),
        }
    }

    #[test]
    fn test_process_not_equal_dict() {
        let entry = EsFilterEntry {
            _key: "status".to_string(),
            field_name: "status".to_string(),
            suffix: String::new(),
            value: EsValue::Dict {
                op: "!=".to_string(),
                val: Box::new(EsValue::Str("deleted".to_string())),
            },
            field_type: None,
        };
        let result = process_es_entry(&entry).unwrap();
        match result {
            EsCondition::MustNotTerm(field, EsValue::Str(val)) => {
                assert_eq!(field, "status");
                assert_eq!(val, "deleted");
            }
            _ => panic!("Expected MustNotTerm condition"),
        }
    }

    #[test]
    fn test_convert_value_to_int() {
        let val = EsValue::Str("42".to_string());
        let ft = Some("integer".to_string());
        match convert_value(&val, &ft) {
            EsValue::Int(i) => assert_eq!(i, 42),
            _ => panic!("Expected Int"),
        }
    }

    #[test]
    fn test_convert_value_to_float() {
        let val = EsValue::Str("3.14".to_string());
        let ft = Some("float".to_string());
        match convert_value(&val, &ft) {
            EsValue::Float(f) => assert!((f - 3.14).abs() < 0.001),
            _ => panic!("Expected Float"),
        }
    }

    #[test]
    fn test_convert_value_to_bool() {
        let val = EsValue::Str("true".to_string());
        let ft = Some("boolean".to_string());
        match convert_value(&val, &ft) {
            EsValue::Bool(b) => assert!(b),
            _ => panic!("Expected Bool"),
        }
    }
}
