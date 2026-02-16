// Copyright (C) 2018-present Jesus Lara
//
// cql_parser.rs — Cassandra CQL filter_conditions with rayon parallelism.
// CQL WHERE syntax follows standard SQL patterns: NULL, IN, NOT IN, BETWEEN,
// negation, equality. Reuses filter_common for all shared logic.

use pyo3::prelude::*;
use pyo3::types::PyDict;
use rayon::prelude::*;

use crate::filter_common::{apply_where_clause, extract_entries, process_entry};

// ---------------------------------------------------------------------------
// Public PyO3 function
// ---------------------------------------------------------------------------

/// Cassandra CQL filter_conditions with parallel processing.
///
/// 1. Extracts filter entries from Python dict into Rust-native structs (GIL)
/// 2. Processes each entry in parallel via rayon (no GIL required)
/// 3. Joins results and applies WHERE clause to CQL template
#[pyfunction]
#[pyo3(signature = (cql, filter_dict, cond_definition))]
pub fn cql_filter_conditions(
    cql: &str,
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

    // Phase 3: Apply WHERE clause to CQL
    apply_where_clause(cql, &where_cond)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filter_common::{FilterEntry, FilterValue};

    #[test]
    fn test_cql_null() {
        let entry = FilterEntry {
            key: "status".to_string(),
            value: FilterValue::Str("null".to_string()),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("status IS NULL".to_string())
        );
    }

    #[test]
    fn test_cql_not_null() {
        let entry = FilterEntry {
            key: "email".to_string(),
            value: FilterValue::Str("!null".to_string()),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("email IS NOT NULL".to_string())
        );
    }

    #[test]
    fn test_cql_equality() {
        let entry = FilterEntry {
            key: "user_name".to_string(),
            value: FilterValue::Str("admin".to_string()),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("user_name = 'admin'".to_string())
        );
    }

    #[test]
    fn test_cql_int() {
        let entry = FilterEntry {
            key: "age".to_string(),
            value: FilterValue::Int(30),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("age = 30".to_string())
        );
    }

    #[test]
    fn test_cql_bool() {
        let entry = FilterEntry {
            key: "is_active".to_string(),
            value: FilterValue::Bool(true),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("is_active = true".to_string())
        );
    }

    #[test]
    fn test_cql_in_list() {
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
    fn test_cql_not_in_list() {
        let entry = FilterEntry {
            key: "status!".to_string(),
            value: FilterValue::List(vec![
                FilterValue::Str("deleted".to_string()),
                FilterValue::Str("archived".to_string()),
            ]),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("status NOT IN ('deleted','archived')".to_string())
        );
    }

    #[test]
    fn test_cql_negation() {
        let entry = FilterEntry {
            key: "type".to_string(),
            value: FilterValue::Str("!admin".to_string()),
            format_hint: None,
        };
        let result = process_entry(&entry).unwrap();
        assert!(result.contains("type != "));
        assert!(result.contains("admin"));
    }

    #[test]
    fn test_cql_keyspace_qualified_table() {
        // Cassandra uses keyspace.table naming
        let result = apply_where_clause(
            "SELECT * FROM mykeyspace.users {filter}",
            &["age = 30".to_string(), "is_active = true".to_string()],
        )
        .unwrap();
        assert_eq!(
            result,
            "SELECT * FROM mykeyspace.users  WHERE age = 30 AND is_active = true"
        );
    }

    #[test]
    fn test_cql_where_clause_empty() {
        let result = apply_where_clause(
            "SELECT * FROM mykeyspace.users {filter}",
            &[],
        )
        .unwrap();
        assert_eq!(result, "SELECT * FROM mykeyspace.users ");
    }

    #[test]
    fn test_cql_existing_where() {
        let result = apply_where_clause(
            "SELECT * FROM users WHERE partition_key = 1",
            &["cluster_col = 'abc'".to_string()],
        )
        .unwrap();
        assert_eq!(
            result,
            "SELECT * FROM users WHERE partition_key = 1 AND cluster_col = 'abc'"
        );
    }

    #[test]
    fn test_cql_date_between() {
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
}
