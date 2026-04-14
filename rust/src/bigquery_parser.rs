// Copyright (C) 2018-present Jesus Lara
//
// bigquery_parser.rs — BigQuery-specific filter_conditions with rayon parallelism.
// Extends the base filter logic with BigQuery JSON support:
// JSON_VALUE(), dot-notation field access, standard SQL WHERE operators.
// Does NOT include PG-specific features (ILIKE, array types, range types, JSONB).

use pyo3::prelude::*;
use pyo3::types::PyDict;
use rayon::prelude::*;
use regex::Regex;
use std::collections::HashMap;
use std::sync::LazyLock;

use crate::filter_common::apply_where_clause;
use crate::safe_dict::safe_format_map_rust;
use crate::validators::{bq_quote_string, field_components, is_integer, quote_string};

/// BigQuery comparison tokens.
const COMPARISON_TOKENS: &[&str] = &[">=", "<=", "<>", "!=", "<", ">"];

/// Valid SQL operators for list-based conditions.
const VALID_OPERATORS: &[&str] = &["<", ">", ">=", "<=", "<>", "!=", "IS NOT", "IS"];

/// Regex for detecting JSON dot-notation fields (e.g., `metadata.region`).
static JSON_DOT_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^([a-zA-Z0-9_]+)\.([a-zA-Z0-9_.]+)$").unwrap());

// ---------------------------------------------------------------------------
// Rust-native types for parallel processing (Send + Sync)
// ---------------------------------------------------------------------------

/// Represents a filter value extracted from Python into Rust-native types.
#[derive(Debug, Clone)]
enum FilterValue {
    Str(String),
    Int(i64),
    Float(f64),
    Bool(bool),
    List(Vec<FilterValue>),
    Dict(Vec<(String, FilterValue)>),
    Null,
}

impl FilterValue {
    /// Extract to a display string.
    fn as_str(&self) -> String {
        match self {
            FilterValue::Str(s) => s.clone(),
            FilterValue::Int(i) => i.to_string(),
            FilterValue::Float(f) => f.to_string(),
            FilterValue::Bool(b) => b.to_string(),
            FilterValue::Null => "NULL".to_string(),
            FilterValue::List(_) => String::new(),
            FilterValue::Dict(_) => String::new(),
        }
    }
}

/// A single filter entry ready for parallel processing.
#[derive(Debug, Clone)]
struct FilterEntry {
    key: String,
    value: FilterValue,
    format_hint: Option<String>,
}

// ---------------------------------------------------------------------------
// JSON field detection
// ---------------------------------------------------------------------------

/// Parse a field key for JSON dot-notation.
/// Returns (field_expression, is_json) where field_expression is either
/// `JSON_VALUE(col, '$.path')` or the raw key.
fn resolve_json_field(key: &str, format_hint: Option<&str>) -> (String, bool) {
    // Check dot-notation first: metadata.region -> JSON_VALUE(metadata, '$.region')
    if let Some(caps) = JSON_DOT_PATTERN.captures(key) {
        let column = &caps[1];
        let path = &caps[2];
        return (
            format!("JSON_VALUE({}, '$.{}')", column, path),
            true,
        );
    }
    // Check format_hint for "json" type
    if format_hint == Some("json") {
        return (format!("JSON_VALUE({}, '$')", key), true);
    }
    (key.to_string(), false)
}

// ---------------------------------------------------------------------------
// Extraction from Python types (runs on GIL thread)
// ---------------------------------------------------------------------------

/// Recursively extract a Python object into a FilterValue.
fn extract_filter_value(obj: &Bound<'_, pyo3::types::PyAny>) -> FilterValue {
    // Order matters: check bool before int (Python bool is a subclass of int)
    if let Ok(b) = obj.extract::<bool>() {
        return FilterValue::Bool(b);
    }
    if let Ok(i) = obj.extract::<i64>() {
        return FilterValue::Int(i);
    }
    if let Ok(f) = obj.extract::<f64>() {
        if f.fract() != 0.0 {
            return FilterValue::Float(f);
        }
        return FilterValue::Int(f as i64);
    }
    if let Ok(s) = obj.extract::<String>() {
        return FilterValue::Str(s);
    }
    if let Ok(dict) = obj.downcast::<PyDict>() {
        let entries: Vec<(String, FilterValue)> = dict
            .iter()
            .filter_map(|(k, v)| {
                k.extract::<String>()
                    .ok()
                    .map(|key| (key, extract_filter_value(&v)))
            })
            .collect();
        return FilterValue::Dict(entries);
    }
    if let Ok(list) = obj.extract::<Vec<Bound<'_, pyo3::types::PyAny>>>() {
        let items: Vec<FilterValue> = list.iter().map(|item| extract_filter_value(item)).collect();
        return FilterValue::List(items);
    }
    // Final fallback: try str()
    if let Ok(s) = obj.str() {
        return FilterValue::Str(s.to_string());
    }
    FilterValue::Null
}

// ---------------------------------------------------------------------------
// Per-entry condition builder (runs in parallel via rayon)
// ---------------------------------------------------------------------------

/// Process a single filter entry into a WHERE condition string.
fn process_entry(entry: &FilterEntry) -> Option<String> {
    let key = &entry.key;
    let value = &entry.value;
    let format_hint = entry.format_hint.as_deref();

    // Quote numeric keys
    let quoted_key = if key.parse::<i64>().is_ok() || is_integer(key) {
        format!("\"{}\"", key)
    } else {
        key.clone()
    };

    // Get field components for suffix detection
    let components = field_components(key);
    let (name, end) = if !components.is_empty() {
        (components[0].1.clone(), components[0].2.clone())
    } else {
        (key.clone(), String::new())
    };

    // Resolve JSON field expression
    let (field_expr, _is_json) = resolve_json_field(&quoted_key, format_hint);

    match value {
        FilterValue::Dict(entries) => {
            process_dict_value(&field_expr, entries)
        }
        FilterValue::List(items) => {
            process_list_value(&field_expr, items, &name, &end, format_hint)
        }
        FilterValue::Str(s) => {
            process_str_value(&field_expr, s, &name, &end)
        }
        FilterValue::Int(i) => Some(format!("{} = {}", field_expr, i)),
        FilterValue::Bool(b) => Some(format!("{} = {}", field_expr, b)),
        FilterValue::Float(f) => Some(format!("{} = {}", field_expr, f)),
        FilterValue::Null => None,
    }
}

/// Handle dict-typed filter values: comparison tokens and JSON extraction.
fn process_dict_value(
    field_expr: &str,
    entries: &[(String, FilterValue)],
) -> Option<String> {
    if entries.is_empty() {
        return None;
    }

    let (op, v) = &entries[0];

    // Standard comparison tokens
    if COMPARISON_TOKENS.contains(&op.as_str()) {
        return Some(format!("{} {} {}", field_expr, op, v.as_str()));
    }

    // For BigQuery, dict values with non-comparison keys are JSON extraction
    // JSON_VALUE(field, '$.key') = value
    if entries.len() == 1 {
        let val_str = v.as_str();
        let json_expr = format!("JSON_VALUE({}, '$.{}')", field_expr, op);
        return Some(format!("{} = {}", json_expr, bq_quote_string(&val_str)));
    }

    None
}

/// Handle list-typed filter values: operators, BETWEEN, IN, NOT IN.
fn process_list_value(
    field_expr: &str,
    items: &[FilterValue],
    name: &str,
    end: &str,
    format_hint: Option<&str>,
) -> Option<String> {
    if items.is_empty() {
        return None;
    }

    let first_str = items[0].as_str();

    // Check if first item is a valid operator
    if VALID_OPERATORS.contains(&first_str.as_str()) && items.len() > 1 {
        return Some(format!("{} {} {}", field_expr, first_str, items[1].as_str()));
    }

    // date/datetime BETWEEN
    if format_hint == Some("date") || format_hint == Some("datetime") {
        if items.len() >= 2 {
            let v0 = items[0].as_str();
            let v1 = items[1].as_str();
            if end == "!" {
                return Some(format!("{} NOT BETWEEN '{}' AND '{}'", name, v0, v1));
            } else {
                return Some(format!("{} BETWEEN '{}' AND '{}'", name, v0, v1));
            }
        }
    }

    // Build IN clause from list values
    let val_str: String = items
        .iter()
        .map(|v| bq_quote_string(&v.as_str()))
        .collect::<Vec<_>>()
        .join(",");

    if end == "!" {
        Some(format!("{} NOT IN ({})", name, val_str))
    } else if val_str.is_empty() {
        Some(format!("{} IN (NULL)", field_expr))
    } else {
        Some(format!("{} IN ({})", field_expr, val_str))
    }
}

/// Handle string-typed filter values with BigQuery SQL syntax.
fn process_str_value(
    field_expr: &str,
    value: &str,
    name: &str,
    end: &str,
) -> Option<String> {
    // BETWEEN in value string
    if value.contains("BETWEEN") {
        return Some(format!("({} {})", field_expr, value));
    }
    // NULL checks
    if value == "null" || value == "NULL" {
        return Some(format!("{} IS NULL", field_expr));
    }
    if value == "!null" || value == "!NULL" {
        return Some(format!("{} IS NOT NULL", field_expr));
    }
    // Negation with end marker
    if end == "!" {
        return Some(format!("{} != {}", name, value));
    }
    // Negation with ! prefix
    if value.starts_with('!') {
        return Some(format!(
            "{} != {}",
            field_expr,
            bq_quote_string(&value[1..])
        ));
    }
    // Default: quoted equality
    Some(format!("{}={}", field_expr, bq_quote_string(value)))
}

// ---------------------------------------------------------------------------
// Public PyO3 function
// ---------------------------------------------------------------------------

/// BigQuery-specific filter_conditions with parallel processing.
///
/// 1. Extracts filter entries from Python dict into Rust-native structs (GIL)
/// 2. Processes each entry in parallel via rayon (no GIL required)
/// 3. Joins results and applies WHERE clause to SQL template
#[pyfunction]
#[pyo3(signature = (sql, filter_dict, cond_definition))]
pub fn bq_filter_conditions(
    sql: &str,
    filter_dict: &Bound<'_, PyDict>,
    cond_definition: &Bound<'_, PyDict>,
) -> PyResult<String> {
    // Phase 1: Extract from Python (serial, holds GIL)
    let entries: Vec<FilterEntry> = filter_dict
        .iter()
        .map(|(key_obj, value_obj)| {
            let key: String = key_obj.extract().unwrap_or_default();
            let format_hint: Option<String> = cond_definition
                .get_item(&key)
                .ok()
                .flatten()
                .and_then(|v| v.extract().ok());
            let value = extract_filter_value(&value_obj);
            FilterEntry {
                key,
                value,
                format_hint,
            }
        })
        .collect();

    // Phase 2: Process in parallel (no GIL, pure Rust)
    let where_cond: Vec<String> = entries
        .par_iter()
        .filter_map(|entry| process_entry(entry))
        .collect();

    // Phase 3: Apply WHERE clause to SQL
    apply_where_clause(sql, &where_cond)
}

/// BigQuery JSON field processing for SELECT clause.
///
/// Transforms fields with dot-notation into JSON_VALUE() expressions.
/// e.g., "metadata.region" -> "JSON_VALUE(metadata, '$.region') AS region"
#[pyfunction]
#[pyo3(signature = (sql, fields, add_fields, query_raw, cond_definition))]
pub fn bq_process_fields(
    sql: &str,
    fields: Vec<String>,
    add_fields: bool,
    query_raw: &str,
    cond_definition: &Bound<'_, PyDict>,
) -> PyResult<String> {
    if fields.is_empty() {
        // No fields to process, check for {fields} placeholder
        if query_raw.contains("{fields}") {
            // Return sql with * substituted
            let mut m = HashMap::new();
            m.insert("fields".to_string(), "*".to_string());
            return Ok(safe_format_map_rust(sql, &m));
        }
        return Ok(sql.to_string());
    }

    // Process each field for JSON dot-notation
    let processed: Vec<String> = fields
        .iter()
        .map(|field| {
            let format_hint: Option<String> = cond_definition
                .get_item(field)
                .ok()
                .flatten()
                .and_then(|v| v.extract().ok());

            let (expr, is_json) = resolve_json_field(field, format_hint.as_deref());

            if is_json && !field.contains(" AS ") && !field.contains(" as ") {
                // Add alias from the last part of the dot-notation
                let alias = field.rsplit('.').next().unwrap_or(field);
                format!("{} AS {}", expr, alias)
            } else {
                expr
            }
        })
        .collect();

    let fields_str = processed.join(", ");

    let mut result = sql.to_string();

    if add_fields {
        // Add fields after existing SELECT fields
        // Find SELECT ... FROM pattern and append
        if let Some(from_idx) = result.to_uppercase().find("FROM") {
            // Find the SELECT fields portion
            let prefix = &result[..from_idx];
            let suffix = &result[from_idx..];
            // Append new fields before FROM
            result = format!("{}, {} {}", prefix.trim_end(), fields_str, suffix);
        }
    } else {
        // Replace * with fields or substitute {fields}
        result = result.replace(" * FROM", &format!(" {{fields}} FROM"));
        let mut m = HashMap::new();
        m.insert("fields".to_string(), fields_str);
        result = safe_format_map_rust(&result, &m);
    }

    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    // -- JSON field detection tests --

    #[test]
    fn test_resolve_json_dot_notation() {
        let (expr, is_json) = resolve_json_field("metadata.region", None);
        assert!(is_json);
        assert_eq!(expr, "JSON_VALUE(metadata, '$.region')");
    }

    #[test]
    fn test_resolve_json_nested_dot() {
        let (expr, is_json) = resolve_json_field("data.address.city", None);
        assert!(is_json);
        assert_eq!(expr, "JSON_VALUE(data, '$.address.city')");
    }

    #[test]
    fn test_resolve_json_format_hint() {
        let (expr, is_json) = resolve_json_field("metadata", Some("json"));
        assert!(is_json);
        assert_eq!(expr, "JSON_VALUE(metadata, '$')");
    }

    #[test]
    fn test_resolve_non_json_field() {
        let (expr, is_json) = resolve_json_field("territory_id", None);
        assert!(!is_json);
        assert_eq!(expr, "territory_id");
    }

    // -- process_entry tests --

    #[test]
    fn test_process_str_null() {
        let entry = FilterEntry {
            key: "status".to_string(),
            value: FilterValue::Str("null".to_string()),
            format_hint: None,
        };
        assert_eq!(process_entry(&entry), Some("status IS NULL".to_string()));
    }

    #[test]
    fn test_process_str_not_null() {
        let entry = FilterEntry {
            key: "status".to_string(),
            value: FilterValue::Str("!null".to_string()),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("status IS NOT NULL".to_string())
        );
    }

    #[test]
    fn test_process_str_negation() {
        let entry = FilterEntry {
            key: "status".to_string(),
            value: FilterValue::Str("!active".to_string()),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("status != 'active'".to_string())
        );
    }

    #[test]
    fn test_process_bool() {
        let entry = FilterEntry {
            key: "active".to_string(),
            value: FilterValue::Bool(true),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("active = true".to_string())
        );
    }

    #[test]
    fn test_process_int() {
        let entry = FilterEntry {
            key: "age".to_string(),
            value: FilterValue::Int(25),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("age = 25".to_string())
        );
    }

    #[test]
    fn test_process_str_equality() {
        let entry = FilterEntry {
            key: "name".to_string(),
            value: FilterValue::Str("John".to_string()),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("name=\"John\"".to_string())
        );
    }

    #[test]
    fn test_process_str_with_single_quote() {
        // NAV-8095: apostrophe in value must produce double-quoted BigQuery string.
        let entry = FilterEntry {
            key: "retailer".to_string(),
            value: FilterValue::Str("Sam's Club".to_string()),
            format_hint: None,
        };
        let result = process_entry(&entry).unwrap();
        assert_eq!(result, "retailer=\"Sam's Club\"");
        assert!(!result.contains("Sams Club"), "apostrophe must not be stripped");
        assert!(!result.contains("Sam''s Club"), "PostgreSQL-style escaping must not appear");
    }

    // -- JSON field filter tests --

    #[test]
    fn test_process_json_dot_notation_equality() {
        let entry = FilterEntry {
            key: "metadata.region".to_string(),
            value: FilterValue::Str("us-east-1".to_string()),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("JSON_VALUE(metadata, '$.region')=\"us-east-1\"".to_string())
        );
    }

    #[test]
    fn test_process_json_dot_notation_null() {
        let entry = FilterEntry {
            key: "data.status".to_string(),
            value: FilterValue::Str("null".to_string()),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("JSON_VALUE(data, '$.status') IS NULL".to_string())
        );
    }

    #[test]
    fn test_process_json_format_hint() {
        let entry = FilterEntry {
            key: "config".to_string(),
            value: FilterValue::Str("active".to_string()),
            format_hint: Some("json".to_string()),
        };
        assert_eq!(
            process_entry(&entry),
            Some("JSON_VALUE(config, '$')=\"active\"".to_string())
        );
    }

    #[test]
    fn test_process_json_dict_extraction() {
        let entry = FilterEntry {
            key: "metadata".to_string(),
            value: FilterValue::Dict(vec![
                ("region".to_string(), FilterValue::Str("us-east-1".to_string())),
            ]),
            format_hint: None,
        };
        let result = process_entry(&entry).unwrap();
        assert!(result.contains("JSON_VALUE"));
        assert!(result.contains("region"));
        assert!(result.contains("us-east-1"));
    }

    // -- List filter tests --

    #[test]
    fn test_process_list_in_clause() {
        let entry = FilterEntry {
            key: "status".to_string(),
            value: FilterValue::List(vec![
                FilterValue::Str("active".to_string()),
                FilterValue::Str("pending".to_string()),
            ]),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("status IN (\"active\",\"pending\")".to_string())
        );
    }

    #[test]
    fn test_process_date_between() {
        let entry = FilterEntry {
            key: "created_at".to_string(),
            value: FilterValue::List(vec![
                FilterValue::Str("2024-01-01".to_string()),
                FilterValue::Str("2024-12-31".to_string()),
            ]),
            format_hint: Some("date".to_string()),
        };
        assert_eq!(
            process_entry(&entry),
            Some("created_at BETWEEN '2024-01-01' AND '2024-12-31'".to_string())
        );
    }

    // -- Comparison token tests --

    #[test]
    fn test_process_comparison_token() {
        let entry = FilterEntry {
            key: "age".to_string(),
            value: FilterValue::Dict(vec![
                (">=".to_string(), FilterValue::Str("18".to_string())),
            ]),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("age >= 18".to_string())
        );
    }

    #[test]
    fn test_process_between_in_string() {
        let entry = FilterEntry {
            key: "date".to_string(),
            value: FilterValue::Str("BETWEEN '2024-01-01' AND '2024-12-31'".to_string()),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("(date BETWEEN '2024-01-01' AND '2024-12-31')".to_string())
        );
    }

    // -- WHERE clause tests --

    #[test]
    fn test_apply_where_clause_filter() {
        let result = apply_where_clause(
            "SELECT * FROM t {filter}",
            &["a=1".to_string()],
        )
        .unwrap();
        assert_eq!(result, "SELECT * FROM t  WHERE a=1");
    }

    #[test]
    fn test_apply_where_clause_existing_where() {
        let result = apply_where_clause(
            "SELECT * FROM t WHERE x=0",
            &["a=1".to_string()],
        )
        .unwrap();
        assert_eq!(result, "SELECT * FROM t WHERE x=0 AND a=1");
    }

    #[test]
    fn test_apply_where_clause_empty() {
        let result = apply_where_clause(
            "SELECT * FROM t {filter}",
            &[],
        )
        .unwrap();
        assert_eq!(result, "SELECT * FROM t ");
    }
}
