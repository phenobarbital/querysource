// Copyright (C) 2018-present Jesus Lara
//
// soql_parser.rs — Salesforce SOQL filter_conditions with rayon parallelism.
// SOQL WHERE syntax is identical to standard SQL: NULL, IN, NOT IN, BETWEEN,
// negation, equality. Reuses filter_common for all shared logic.

use pyo3::prelude::*;
use pyo3::types::PyDict;
use rayon::prelude::*;

use crate::filter_common::{apply_where_clause, extract_entries, process_entry};

// ---------------------------------------------------------------------------
// Public PyO3 function
// ---------------------------------------------------------------------------

/// Salesforce SOQL filter_conditions with parallel processing.
///
/// 1. Extracts filter entries from Python dict into Rust-native structs (GIL)
/// 2. Processes each entry in parallel via rayon (no GIL required)
/// 3. Joins results and applies WHERE clause to SQL template
#[pyfunction]
#[pyo3(signature = (sql, filter_dict, cond_definition))]
pub fn soql_filter_conditions(
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
    fn test_soql_null() {
        let entry = FilterEntry {
            key: "Account.Name".to_string(),
            value: FilterValue::Str("null".to_string()),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("Account.Name IS NULL".to_string())
        );
    }

    #[test]
    fn test_soql_not_null() {
        let entry = FilterEntry {
            key: "Email".to_string(),
            value: FilterValue::Str("!null".to_string()),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("Email IS NOT NULL".to_string())
        );
    }

    #[test]
    fn test_soql_equality() {
        let entry = FilterEntry {
            key: "Name".to_string(),
            value: FilterValue::Str("Acme Corp".to_string()),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("Name = 'Acme Corp'".to_string())
        );
    }

    #[test]
    fn test_soql_int() {
        let entry = FilterEntry {
            key: "NumberOfEmployees".to_string(),
            value: FilterValue::Int(100),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("NumberOfEmployees = 100".to_string())
        );
    }

    #[test]
    fn test_soql_bool() {
        let entry = FilterEntry {
            key: "IsActive".to_string(),
            value: FilterValue::Bool(true),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("IsActive = true".to_string())
        );
    }

    #[test]
    fn test_soql_in_list() {
        let entry = FilterEntry {
            key: "Status".to_string(),
            value: FilterValue::List(vec![
                FilterValue::Str("Open".to_string()),
                FilterValue::Str("Closed".to_string()),
            ]),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("Status IN ('Open','Closed')".to_string())
        );
    }

    #[test]
    fn test_soql_date_between() {
        let entry = FilterEntry {
            key: "CreatedDate".to_string(),
            value: FilterValue::List(vec![
                FilterValue::Str("2024-01-01".to_string()),
                FilterValue::Str("2024-12-31".to_string()),
            ]),
            format_hint: Some("date".to_string()),
        };
        assert_eq!(
            process_entry(&entry),
            Some("CreatedDate BETWEEN '2024-01-01' AND '2024-12-31'".to_string())
        );
    }

    #[test]
    fn test_soql_negation() {
        let entry = FilterEntry {
            key: "Type".to_string(),
            value: FilterValue::Str("!Customer".to_string()),
            format_hint: None,
        };
        let result = process_entry(&entry).unwrap();
        assert!(result.contains("Type != "));
        assert!(result.contains("Customer"));
    }

    #[test]
    fn test_soql_where_clause() {
        let result = apply_where_clause(
            "SELECT Id, Name FROM Account {filter}",
            &["Name = 'Acme'".to_string(), "IsActive = true".to_string()],
        )
        .unwrap();
        assert_eq!(
            result,
            "SELECT Id, Name FROM Account  WHERE Name = 'Acme' AND IsActive = true"
        );
    }

    #[test]
    fn test_soql_where_clause_empty() {
        let result = apply_where_clause(
            "SELECT Id FROM Account {filter}",
            &[],
        )
        .unwrap();
        assert_eq!(result, "SELECT Id FROM Account ");
    }
}
