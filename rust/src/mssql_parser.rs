// Copyright (C) 2018-present Jesus Lara
//
// mssql_parser.rs — MS SQL Server filter_conditions with rayon parallelism.
// Uses shared types from filter_common.

use pyo3::prelude::*;
use pyo3::types::PyDict;
use rayon::prelude::*;

use crate::filter_common::{apply_where_clause, extract_entries, process_entry};

// ---------------------------------------------------------------------------
// Public PyO3 function
// ---------------------------------------------------------------------------

/// MS SQL Server filter_conditions with parallel processing.
///
/// 1. Extracts filter entries from Python dict into Rust-native structs (GIL)
/// 2. Processes each entry in parallel via rayon (no GIL required)
/// 3. Joins results and applies WHERE clause to SQL template
#[pyfunction]
#[pyo3(signature = (sql, filter_dict, cond_definition))]
pub fn mssql_filter_conditions(
    sql: &str,
    filter_dict: &Bound<'_, PyDict>,
    cond_definition: &Bound<'_, PyDict>,
) -> PyResult<String> {
    // Phase 1: Extract from Python (serial, holds GIL)
    let entries = extract_entries(filter_dict, cond_definition);

    // Phase 2: Process in parallel (no GIL, pure Rust)
    let where_cond: Vec<String> = entries
        .par_iter()
        .filter_map(|entry| process_entry(entry))
        .collect();

    // Phase 3: Apply WHERE clause to SQL
    apply_where_clause(sql, &where_cond)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filter_common::{FilterEntry, FilterValue};

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
