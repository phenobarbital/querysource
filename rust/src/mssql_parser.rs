// Copyright (C) 2018-present Jesus Lara
//
// mssql_parser.rs — MS SQL Server filter_conditions with rayon parallelism.
// Uses shared types from filter_common.

use pyo3::prelude::*;
use pyo3::types::PyDict;
use rayon::prelude::*;

use crate::filter_common::{apply_where_clause, extract_filter_value, process_entry, FilterEntry};
use crate::partial_match::{build_like_pattern, check_entries, like_escape_bang, mssql_like_literal, MatchKind, PartialMatchOp};

// ---------------------------------------------------------------------------
// Public PyO3 function
// ---------------------------------------------------------------------------

enum MssqlItem {
    Done(String),
    Entry(FilterEntry),
}

/// Render one partial-matching operator for SQL Server (FEAT-180, spec section 2).
fn mssql_partial_match_condition(col: &str, op: &PartialMatchOp, operand: &str) -> PyResult<String> {
    if op.kind == MatchKind::Regex {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "{} on '{}': regex operators are not supported by this query parser",
            op.name, col
        )));
    }
    let like = if op.negated { "NOT LIKE" } else { "LIKE" };
    let lit = mssql_like_literal(&build_like_pattern(op, operand, like_escape_bang));
    let esc = if op.escape { " ESCAPE '!'" } else { "" };
    Ok(if op.insensitive {
        format!("LOWER({}) {} LOWER({}){}", col, like, lit, esc)
    } else {
        format!("{} {} {}{}", col, like, lit, esc)
    })
}

/// Same safe-identifier rule as sqlserver.pyx (alnum, '_' or '.').
fn is_safe_key(key: &str) -> bool {
    key.trim_end_matches(|c| "|!~#@:".contains(c))
        .chars()
        .all(|c| c.is_alphanumeric() || c == '_' || c == '.')
}

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
    // Phase 1: serial, holds the GIL; partial-matching dicts are validated and rendered here.
    let mut items: Vec<MssqlItem> = Vec::with_capacity(filter_dict.len());
    for (key_obj, value_obj) in filter_dict.iter() {
        let key: String = key_obj.extract().unwrap_or_default();
        if let Ok(d) = value_obj.cast::<PyDict>() {
            let pairs: Vec<(String, Option<String>)> = d
                .iter()
                .map(|(k, v)| {
                    (
                        k.extract::<String>().unwrap_or_default(),
                        v.extract::<String>().ok(),
                    )
                })
                .collect();
            let refs: Vec<(&str, Option<&str>)> = pairs
                .iter()
                .map(|(k, v)| (k.as_str(), v.as_deref()))
                .collect();
            if let Some((op, operand)) = check_entries(&key, &refs, false)? {
                if is_safe_key(&key) {
                    items.push(MssqlItem::Done(mssql_partial_match_condition(&key, op, operand)?));
                }
                continue;
            }
        }
        let format_hint: Option<String> = cond_definition
            .get_item(&key)
            .ok()
            .flatten()
            .and_then(|v| v.extract().ok());
        let value = extract_filter_value(&value_obj);
        items.push(MssqlItem::Entry(FilterEntry {
            key,
            value,
            format_hint,
        }));
    }

    // Phase 2: parallel, order-preserving.
    let where_cond: Vec<String> = items
        .par_iter()
        .filter_map(|item| match item {
            MssqlItem::Done(c) => Some(c.clone()),
            MssqlItem::Entry(e) => process_entry(e),
        })
        .collect();

    // Phase 3: Apply WHERE clause to SQL
    apply_where_clause(sql, &where_cond)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filter_common::{FilterEntry, FilterValue};

    #[test]
    fn test_pm_mssql_startswith_bracket() {
        let op = crate::partial_match::lookup("startswith").unwrap();
        assert_eq!(mssql_partial_match_condition("n", op, "a[b").unwrap(), "n LIKE 'a![b%' ESCAPE '!'");
    }

    #[test]
    fn test_pm_mssql_icontains() {
        let op = crate::partial_match::lookup("icontains").unwrap();
        assert_eq!(
            mssql_partial_match_condition("n", op, "abc").unwrap(),
            "LOWER(n) LIKE LOWER('%abc%') ESCAPE '!'"
        );
    }

    #[test]
    fn test_pm_mssql_negated() {
        let op = crate::partial_match::lookup("not_startswith").unwrap();
        assert_eq!(
            mssql_partial_match_condition("n", op, "ab").unwrap(),
            "n NOT LIKE 'ab%' ESCAPE '!'"
        );
    }

    #[test]
    fn test_pm_mssql_regex_err() {
        let op = crate::partial_match::lookup("regex").unwrap();
        assert!(mssql_partial_match_condition("n", op, "a.*").is_err());
    }

    #[test]
    fn test_pm_mssql_safe_key() {
        assert!(is_safe_key("a.b_c!"));
        assert!(!is_safe_key("a;b"));
    }

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
