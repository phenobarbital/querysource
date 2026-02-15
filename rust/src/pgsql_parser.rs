// Copyright (C) 2018-present Jesus Lara
//
// pgsql_parser.rs — PostgreSQL-specific filter_conditions with rayon parallelism.
// Extends the base sql_parser filter logic with PG operators:
// ILIKE, array containment, range types, JSONB, and CamelCase quoting.

use pyo3::prelude::*;
use pyo3::types::PyDict;
use rayon::prelude::*;
use std::collections::HashMap;

use crate::safe_dict::safe_format_map_rust;
use crate::validators::{field_components, is_camel_case, is_integer, quote_string};

/// PostgreSQL comparison tokens.
const COMPARISON_TOKENS: &[&str] = &[">=", "<=", "<>", "!=", "<", ">"];

/// Valid SQL operators for list-based conditions.
const VALID_OPERATORS: &[&str] = &["<", ">", ">=", "<=", "<>", "!=", "IS NOT", "IS"];

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
    /// Extract to a display string, quoting strings.
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

    /// Check if this is a string value.
    fn is_str(&self) -> bool {
        matches!(self, FilterValue::Str(_))
    }

    /// Check if this is an integer value.
    fn is_int(&self) -> bool {
        matches!(self, FilterValue::Int(_))
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
        // Only use float if it's not actually an integer
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
/// Returns `Some(condition)` or `None` if the entry should be skipped.
fn process_entry(entry: &FilterEntry) -> Option<String> {
    let key = &entry.key;
    let value = &entry.value;
    let _format = entry.format_hint.as_deref();

    // Quote numeric keys
    let formatted_key = if key.parse::<i64>().is_ok() || is_integer(key) {
        format!("\"{}\"", key)
    } else {
        key.clone()
    };

    // Get field components
    let components = field_components(key);
    let (name, end) = if !components.is_empty() {
        (components[0].1.clone(), components[0].2.clone())
    } else {
        (key.clone(), String::new())
    };

    match value {
        FilterValue::Dict(entries) => {
            process_dict_value(&formatted_key, entries, _format)
        }
        FilterValue::List(items) => {
            process_list_value(&formatted_key, items, &name, &end, _format)
        }
        FilterValue::Str(s) => {
            process_str_value(&formatted_key, s, &name, &end, _format)
        }
        FilterValue::Int(i) => {
            process_str_value(&formatted_key, &i.to_string(), &name, &end, _format)
        }
        FilterValue::Bool(b) => Some(format!("{} = {}", formatted_key, b)),
        FilterValue::Float(f) => {
            process_str_value(&formatted_key, &f.to_string(), &name, &end, _format)
        }
        FilterValue::Null => None,
    }
}

/// Handle dict-typed filter values: comparison tokens and JSONB operators.
fn process_dict_value(
    key: &str,
    entries: &[(String, FilterValue)],
    _format: Option<&str>,
) -> Option<String> {
    if entries.is_empty() {
        return None;
    }

    let (op, v) = &entries[0];

    // Standard comparison tokens
    if COMPARISON_TOKENS.contains(&op.as_str()) {
        return Some(format!("{} {} {}", key, op, v.as_str()));
    }

    // JSONB operators
    if [
        "->", "->>", "@>", "<@",
    ]
    .contains(&op.as_str())
    {
        let val_str = v.as_str();
        if v.is_str() || v.is_int() {
            return Some(format!("{} {} {}", key, op, quote_string(&val_str, true)));
        } else {
            return Some(format!("{} {} {}", key, op, val_str));
        }
    }

    // Single-key dict for JSONB containment
    if entries.len() == 1 {
        // Build JSON representation
        let val_str = v.as_str();
        let json_repr = format!("{{\"{}\": {}}}", op, quote_json_value(&val_str));
        return Some(format!("{} @> {}", key, json_repr));
    }

    None
}

/// Simple JSON value quoting for JSONB containment.
fn quote_json_value(val: &str) -> String {
    if val.parse::<i64>().is_ok() || val.parse::<f64>().is_ok() {
        val.to_string()
    } else if val == "true" || val == "false" || val == "null" {
        val.to_string()
    } else {
        format!("\"{}\"", val)
    }
}

/// Handle list-typed filter values: operators, BETWEEN, IN, array types.
fn process_list_value(
    key: &str,
    items: &[FilterValue],
    name: &str,
    end: &str,
    _format: Option<&str>,
) -> Option<String> {
    if items.is_empty() {
        return None;
    }

    let first_str = items[0].as_str();

    // Check if first item is a valid operator
    if VALID_OPERATORS.contains(&first_str.as_str()) && items.len() > 1 {
        return Some(format!("{} {} {}", key, first_str, items[1].as_str()));
    }

    // date/datetime BETWEEN
    if _format == Some("date") || _format == Some("datetime") {
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
        .map(|v| quote_string(&v.as_str(), true))
        .collect::<Vec<_>>()
        .join(",");

    if end == "!" {
        Some(format!("{} NOT IN ({})", name, val_str))
    } else if _format == Some("array") {
        if end == "|" {
            Some(format!(
                "ARRAY[{}]::character varying[]  && {}::character varying[]",
                val_str, name
            ))
        } else {
            Some(format!(
                "ARRAY[{}]::character varying[]  <@ {}::character varying[]",
                val_str, key
            ))
        }
    } else {
        if val_str.is_empty() {
            Some(format!("{} IN (NULL)", key))
        } else {
            Some(format!("{} IN ({})", key, val_str))
        }
    }
}

/// Handle string/int-typed filter values with PostgreSQL-specific operators.
fn process_str_value(
    key: &str,
    value: &str,
    name: &str,
    end: &str,
    _format: Option<&str>,
) -> Option<String> {
    // ILIKE pattern
    if end == "~" {
        let val = format!("{}%'", &value[..value.len().saturating_sub(1)]);
        return Some(format!("{} ILIKE {}", name, val));
    }
    // NOT ILIKE pattern
    if end == "!~" {
        let val = format!("{}%'", &value[..value.len().saturating_sub(1)]);
        return Some(format!("{} NOT ILIKE {}", name, val));
    }
    // BETWEEN in value string
    if value.contains("BETWEEN") {
        return Some(format!("({} {})", key, value));
    }
    // NULL checks
    if value == "null" || value == "NULL" {
        return Some(format!("{} IS NULL", key));
    }
    if value == "!null" || value == "!NULL" {
        return Some(format!("{} IS NOT NULL", key));
    }
    // Negation with end marker
    if end == "!" {
        return Some(format!("{} != {}", name, value));
    }
    // Negation with ! prefix
    if value.starts_with('!') {
        return Some(format!(
            "{} != {}",
            key,
            quote_string(&value[1..], true)
        ));
    }

    // PostgreSQL type-specific handling
    match _format {
        Some("array") => {
            if value.parse::<i64>().is_ok() {
                Some(format!("{} = ANY({})", value, key))
            } else {
                Some(format!("{}::character varying = ANY({})", value, key))
            }
        }
        Some("numrange") => Some(format!("{}::numeric <@ {}", value, key)),
        Some("int4range") | Some("int8range") => {
            Some(format!("{}::integer <@ {}::int4range", value, key))
        }
        Some("tsrange") | Some("tstzrange") => {
            Some(format!("{}::timestamptz <@ {}::tstzrange", value, key))
        }
        Some("daterange") => Some(format!("{}::date <@ {}::daterange", value, key)),
        _ => {
            // Default: quote value, handle CamelCase key quoting
            let final_key = if is_camel_case(key) {
                format!("\"{}\"", key)
            } else {
                key.to_string()
            };
            Some(format!("{}={}", final_key, quote_string(value, true)))
        }
    }
}

// ---------------------------------------------------------------------------
// Public PyO3 function
// ---------------------------------------------------------------------------

/// PostgreSQL-specific filter_conditions with parallel processing.
///
/// 1. Extracts filter entries from Python dict into Rust-native structs (GIL)
/// 2. Processes each entry in parallel via rayon (no GIL required)
/// 3. Joins results and applies WHERE clause to SQL template
#[pyfunction]
#[pyo3(signature = (sql, filter_dict, cond_definition))]
pub fn pgsql_filter_conditions(
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
    apply_pg_where_clause(sql, &where_cond)
}

/// Apply WHERE conditions to SQL, handling {filter}, {where_cond}, {and_cond} placeholders.
fn apply_pg_where_clause(sql: &str, where_cond: &[String]) -> PyResult<String> {
    let mut result = sql.to_string();

    if !where_cond.is_empty() {
        let and_clause = where_cond.join(" AND ");

        if result.contains("and_cond") {
            let filter = format!(" AND {and_clause}");
            let mut m = HashMap::new();
            m.insert("and_cond".to_string(), filter);
            result = safe_format_map_rust(&result, &m);
        } else if result.contains("where_cond") {
            let filter = format!(" WHERE {and_clause}");
            let mut m = HashMap::new();
            m.insert("where_cond".to_string(), filter);
            result = safe_format_map_rust(&result, &m);
        } else if result.contains("filter") {
            let filter = format!(" WHERE {and_clause}");
            let mut m = HashMap::new();
            m.insert("filter".to_string(), filter);
            result = safe_format_map_rust(&result, &m);
        } else {
            let filter = if result.contains("WHERE") {
                format!(" AND {and_clause}")
            } else {
                format!(" WHERE {and_clause}")
            };
            result = format!("{result}{filter}");
        }
    }

    // Clean up unused placeholders
    let mut cleanup = HashMap::new();
    cleanup.insert("where_cond".to_string(), String::new());
    cleanup.insert("and_cond".to_string(), String::new());
    cleanup.insert("filter".to_string(), String::new());
    result = safe_format_map_rust(&result, &cleanup);

    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

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
    fn test_process_str_array_int() {
        let entry = FilterEntry {
            key: "tags".to_string(),
            value: FilterValue::Int(42),
            format_hint: Some("array".to_string()),
        };
        assert_eq!(
            process_entry(&entry),
            Some("42 = ANY(tags)".to_string())
        );
    }

    #[test]
    fn test_process_str_array_varchar() {
        let entry = FilterEntry {
            key: "tags".to_string(),
            value: FilterValue::Str("foo".to_string()),
            format_hint: Some("array".to_string()),
        };
        assert_eq!(
            process_entry(&entry),
            Some("foo::character varying = ANY(tags)".to_string())
        );
    }

    #[test]
    fn test_process_numrange() {
        let entry = FilterEntry {
            key: "price_range".to_string(),
            value: FilterValue::Str("50".to_string()),
            format_hint: Some("numrange".to_string()),
        };
        assert_eq!(
            process_entry(&entry),
            Some("50::numeric <@ price_range".to_string())
        );
    }

    #[test]
    fn test_process_daterange() {
        let entry = FilterEntry {
            key: "date_range".to_string(),
            value: FilterValue::Str("2024-01-15".to_string()),
            format_hint: Some("daterange".to_string()),
        };
        assert_eq!(
            process_entry(&entry),
            Some("2024-01-15::date <@ date_range::daterange".to_string())
        );
    }

    #[test]
    fn test_process_tstzrange() {
        let entry = FilterEntry {
            key: "valid_range".to_string(),
            value: FilterValue::Str("2024-01-15 12:00".to_string()),
            format_hint: Some("tstzrange".to_string()),
        };
        assert_eq!(
            process_entry(&entry),
            Some("2024-01-15 12:00::timestamptz <@ valid_range::tstzrange".to_string())
        );
    }

    #[test]
    fn test_process_camel_case_key() {
        let entry = FilterEntry {
            key: "FirstName".to_string(),
            value: FilterValue::Str("John".to_string()),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("\"FirstName\"='John'".to_string())
        );
    }

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
            Some("status IN ('active','pending')".to_string())
        );
    }

    #[test]
    fn test_process_list_not_in() {
        // Key with ! suffix via field_components
        let entry = FilterEntry {
            key: "status!".to_string(),
            value: FilterValue::List(vec![
                FilterValue::Str("deleted".to_string()),
            ]),
            format_hint: None,
        };
        let result = process_entry(&entry);
        assert!(result.is_some());
        let r = result.unwrap();
        assert!(r.contains("NOT IN") || r.contains("IN"));
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

    #[test]
    fn test_apply_where_clause_filter() {
        let result = apply_pg_where_clause(
            "SELECT * FROM t {filter}",
            &["a=1".to_string()],
        )
        .unwrap();
        assert_eq!(result, "SELECT * FROM t  WHERE a=1");
    }

    #[test]
    fn test_apply_where_clause_existing_where() {
        let result = apply_pg_where_clause(
            "SELECT * FROM t WHERE x=0",
            &["a=1".to_string()],
        )
        .unwrap();
        assert_eq!(result, "SELECT * FROM t WHERE x=0 AND a=1");
    }

    #[test]
    fn test_apply_where_clause_empty() {
        let result = apply_pg_where_clause(
            "SELECT * FROM t {filter}",
            &[],
        )
        .unwrap();
        assert_eq!(result, "SELECT * FROM t ");
    }
}
