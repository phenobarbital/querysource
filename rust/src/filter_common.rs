// Copyright (C) 2018-present Jesus Lara
//
// filter_common.rs — Shared filter types and functions for SQL-like parsers.
// Used by mssql_parser, soql_parser, and any future parsers with standard
// WHERE clause logic (NULL, IN, NOT IN, BETWEEN, negation, equality).

use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::collections::HashMap;

use crate::safe_dict::safe_format_map_rust;
use crate::validators::{escape_string, field_components, quote_string};

// ---------------------------------------------------------------------------
// Rust-native types for parallel processing (Send + Sync)
// ---------------------------------------------------------------------------

/// Represents a filter value extracted from Python.
#[derive(Debug, Clone)]
pub enum FilterValue {
    Str(String),
    Int(i64),
    Float(f64),
    Bool(bool),
    List(Vec<FilterValue>),
    Null,
}

impl FilterValue {
    pub fn as_str(&self) -> String {
        match self {
            FilterValue::Str(s) => s.clone(),
            FilterValue::Int(i) => i.to_string(),
            FilterValue::Float(f) => f.to_string(),
            FilterValue::Bool(b) => b.to_string(),
            FilterValue::Null => "NULL".to_string(),
            FilterValue::List(_) => String::new(),
        }
    }
}

/// A single filter entry ready for parallel processing.
#[derive(Debug, Clone)]
pub struct FilterEntry {
    pub key: String,
    pub value: FilterValue,
    pub format_hint: Option<String>,
}

// ---------------------------------------------------------------------------
// Extraction from Python types (runs on GIL thread)
// ---------------------------------------------------------------------------

/// Recursively extract a Python object into a FilterValue.
pub fn extract_filter_value(obj: &Bound<'_, pyo3::types::PyAny>) -> FilterValue {
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
    if let Ok(list) = obj.extract::<Vec<Bound<'_, pyo3::types::PyAny>>>() {
        let items: Vec<FilterValue> = list.iter().map(|item| extract_filter_value(item)).collect();
        return FilterValue::List(items);
    }
    if let Ok(s) = obj.str() {
        return FilterValue::Str(s.to_string());
    }
    FilterValue::Null
}

/// Extract filter entries from a Python dict (serial, holds GIL).
pub fn extract_entries(
    filter_dict: &Bound<'_, PyDict>,
    cond_definition: &Bound<'_, PyDict>,
) -> Vec<FilterEntry> {
    filter_dict
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
        .collect()
}

// ---------------------------------------------------------------------------
// Per-entry condition builder (runs in parallel via rayon)
// ---------------------------------------------------------------------------

/// Process a single filter entry into a WHERE condition string.
pub fn process_entry(entry: &FilterEntry) -> Option<String> {
    let key = &entry.key;
    let value = &entry.value;
    let _format = entry.format_hint.as_deref();

    // Get field components for suffix detection
    let components = field_components(key);
    let (name, end) = if !components.is_empty() {
        (components[0].1.clone(), components[0].2.clone())
    } else {
        (key.clone(), String::new())
    };

    match value {
        FilterValue::List(items) => process_list_value(key, items, &name, &end, _format),
        FilterValue::Str(s) => process_str_value(key, s, &name, &end),
        FilterValue::Int(i) => Some(format!("{} = {}", key, i)),
        FilterValue::Bool(b) => Some(format!("{} = {}", key, b)),
        FilterValue::Float(f) => Some(format!("{} = {}", key, f)),
        FilterValue::Null => None,
    }
}

/// Handle list-typed filter values: IN, NOT IN, date BETWEEN.
pub fn process_list_value(
    key: &str,
    items: &[FilterValue],
    name: &str,
    end: &str,
    _format: Option<&str>,
) -> Option<String> {
    if items.is_empty() {
        return None;
    }

    // Build quoted value list
    let val_str: String = items
        .iter()
        .map(|v| quote_string(&v.as_str(), true))
        .collect::<Vec<_>>()
        .join(",");

    if end == "!" {
        // NOT IN
        Some(format!("{} NOT IN ({})", name, val_str))
    } else if _format == Some("date") {
        // Date BETWEEN
        if items.len() >= 2 {
            Some(format!(
                "{} BETWEEN '{}' AND '{}'",
                key,
                items[0].as_str(),
                items[1].as_str()
            ))
        } else {
            Some(format!("{} IN ({})", key, val_str))
        }
    } else {
        Some(format!("{} IN ({})", key, val_str))
    }
}

/// Handle string-typed filter values with standard SQL syntax.
pub fn process_str_value(key: &str, value: &str, name: &str, end: &str) -> Option<String> {
    // BETWEEN in value string
    if value.contains("BETWEEN") {
        if !value.contains('\'') {
            return Some(format!("({} {})", key, quote_string(value, true)));
        }
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
        let escaped = escape_string(&value[1..]);
        return Some(format!("{} != {}", key, escaped));
    }
    // Default: quoted equality
    Some(format!("{} = {}", key, quote_string(value, true)))
}

// ---------------------------------------------------------------------------
// WHERE clause application (shared by all standard-SQL parsers)
// ---------------------------------------------------------------------------

/// Apply WHERE conditions to SQL, handling {filter}, {where_cond}, {and_cond} placeholders.
pub fn apply_where_clause(sql: &str, where_cond: &[String]) -> PyResult<String> {
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
    fn test_process_str_negation_prefix() {
        let entry = FilterEntry {
            key: "status".to_string(),
            value: FilterValue::Str("!active".to_string()),
            format_hint: None,
        };
        let result = process_entry(&entry).unwrap();
        assert!(result.contains("status != "));
        assert!(result.contains("active"));
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
            Some("name = 'John'".to_string())
        );
    }

    #[test]
    fn test_process_list_in() {
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
