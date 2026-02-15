// Copyright (C) 2018-present Jesus Lara
//
// sql_parser.rs — Rust reimplementation of querysource/parsers/sql.pyx
// Core SQL processing functions with rayon parallelism for build_sql().

use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::collections::HashMap;

use crate::safe_dict::safe_format_map_rust;
use crate::validators::{field_components, quote_string};

/// Comparison tokens recognized in filter conditions.
const COMPARISON_TOKENS: &[&str] = &[">=", "<=", "<>", "!=", "<", ">"];

/// Valid SQL operators for list-based conditions.
const VALID_OPERATORS: &[&str] = &["<", ">", ">=", "<=", "<>", "!=", "IS NOT", "IS"];

// NOTE: Rust regex crate does not support lookaheads.
// We use manual string parsing for GROUP BY and SELECT..FROM extraction.

/// Find the starting position of a SQL keyword (case-insensitive, word-boundary aware).
fn find_keyword(upper_sql: &str, keyword: &str) -> Option<usize> {
    let keyword_upper = keyword.to_uppercase();
    let bytes = upper_sql.as_bytes();
    let kw_bytes = keyword_upper.as_bytes();
    let kw_len = kw_bytes.len();

    for i in 0..=bytes.len().saturating_sub(kw_len) {
        if &bytes[i..i + kw_len] == kw_bytes {
            // Check word boundary before
            let before_ok = i == 0 || !bytes[i - 1].is_ascii_alphanumeric();
            // Check word boundary after
            let after_ok =
                i + kw_len >= bytes.len() || !bytes[i + kw_len].is_ascii_alphanumeric();
            if before_ok && after_ok {
                return Some(i);
            }
        }
    }
    None
}

/// Find the earliest occurrence of any keyword in a string, returning its offset.
fn find_first_keyword(upper_sql: &str, keywords: &[&str]) -> Option<usize> {
    keywords
        .iter()
        .filter_map(|kw| find_keyword(upper_sql, kw))
        .min()
}


// ---------------------------------------------------------------------------
// Individual SQL clause builders
// ---------------------------------------------------------------------------

/// Build WHERE clauses from a filter map.
///
/// Mirrors `SQLParser.filter_conditions()` from sql.pyx.
#[pyfunction]
#[pyo3(signature = (sql, filter_dict, cond_definition))]
pub fn filter_conditions(
    sql: &str,
    filter_dict: &Bound<'_, PyDict>,
    cond_definition: &Bound<'_, PyDict>,
) -> PyResult<String> {
    let mut where_cond: Vec<String> = Vec::new();

    for (key_obj, value_obj) in filter_dict.iter() {
        let key: String = key_obj.extract()?;

        // Check if key is numeric → quote it
        let formatted_key = if key.parse::<i64>().is_ok() {
            format!("\"{key}\"")
        } else {
            key.clone()
        };

        // Get format hint from cond_definition (unused in this function currently)
        let _format: Option<String> = cond_definition
            .get_item(&key)?
            .and_then(|v| v.extract().ok());

        // Parse field_components for the key
        let components = field_components(&key);
        let (_, name, end) = if !components.is_empty() {
            components[0].clone()
        } else {
            ("".to_string(), key.clone(), "".to_string())
        };

        // Handle different value types
        if let Ok(dict_val) = value_obj.downcast::<PyDict>() {
            // Dict value → comparison operator
            if let Some((op_obj, v_obj)) = dict_val.iter().next() {
                let op: String = op_obj.extract()?;
                let v: String = v_obj.extract().unwrap_or_default();
                if COMPARISON_TOKENS.contains(&op.as_str()) {
                    where_cond.push(format!("{formatted_key} {op} {v}"));
                }
                // Non-supported comparison tokens are discarded
            }
        } else if let Ok(list_val) = value_obj.extract::<Vec<String>>() {
            if !list_val.is_empty() {
                let first = &list_val[0];
                if VALID_OPERATORS.contains(&first.as_str()) && list_val.len() > 1 {
                    where_cond.push(format!(
                        "{formatted_key} {first} {}",
                        list_val[1]
                    ));
                } else {
                    // IN clause
                    let val_str: String = list_val
                        .iter()
                        .map(|v| quote_string(v, true))
                        .collect::<Vec<_>>()
                        .join(",");
                    if end == "!" {
                        where_cond.push(format!("{name} NOT IN ({val_str})"));
                    } else {
                        where_cond.push(format!("{formatted_key} IN ({val_str})"));
                    }
                }
            }
        } else if let Ok(bool_val) = value_obj.extract::<bool>() {
            where_cond.push(format!("{formatted_key} = {bool_val}"));
        } else if let Ok(str_val) = value_obj.extract::<String>() {
            build_string_condition(
                &formatted_key,
                &str_val,
                &name,
                &end,
                &mut where_cond,
            );
        } else if let Ok(int_val) = value_obj.extract::<i64>() {
            let str_val = int_val.to_string();
            build_string_condition(
                &formatted_key,
                &str_val,
                &name,
                &end,
                &mut where_cond,
            );
        } else {
            // Fallback: extract as string and quote
            if let Ok(s) = value_obj.str() {
                let s_str = s.to_string();
                where_cond.push(format!(
                    "{formatted_key}={}",
                    quote_string(&s_str, true)
                ));
            }
        }
    }

    apply_where_clause(sql, &where_cond)
}

/// Build a WHERE condition from a string or integer value.
fn build_string_condition(
    key: &str,
    value: &str,
    name: &str,
    end: &str,
    where_cond: &mut Vec<String>,
) {
    if value.contains("BETWEEN") {
        where_cond.push(format!("({key} {value})"));
    } else if value == "null" || value == "NULL" {
        where_cond.push(format!("{key} IS NULL"));
    } else if value == "!null" || value == "!NULL" {
        where_cond.push(format!("{key} IS NOT NULL"));
    } else if end == "!" {
        where_cond.push(format!("{name} != {value}"));
    } else if value.starts_with('!') {
        where_cond.push(format!(
            "{key} != {}",
            quote_string(&value[1..], true)
        ));
    } else {
        where_cond.push(format!("{key}={}", quote_string(value, true)));
    }
}

/// Apply WHERE conditions to SQL, handling {filter}, {where_cond}, {and_cond} placeholders.
fn apply_where_clause(sql: &str, where_cond: &[String]) -> PyResult<String> {
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
            // Attach condition directly
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

/// Build GROUP BY clause.
///
/// Mirrors `SQLParser.group_by()` from sql.pyx.
#[pyfunction]
#[pyo3(signature = (sql, grouping))]
pub fn group_by(sql: &str, grouping: Vec<String>) -> String {
    if grouping.is_empty() {
        return sql.to_string();
    }

    let upper = sql.to_uppercase();
    if let Some(gb_pos) = find_keyword(&upper, "GROUP BY") {
        // Find the column list after GROUP BY
        let after_gb = gb_pos + 8; // len("GROUP BY")
        // Columns end at the next keyword or end of string
        let end_pos = find_first_keyword(&upper[after_gb..], &["HAVING", "ORDER", "LIMIT", "WHERE"])
            .map(|p| after_gb + p)
            .unwrap_or(sql.len());

        let current_cols: Vec<&str> =
            sql[after_gb..end_pos].split(',').map(|c| c.trim()).collect();
        let mut all_cols: Vec<String> =
            current_cols.iter().map(|s| s.to_string()).collect();
        all_cols.extend(grouping);

        format!("{} GROUP BY {} {}",
            sql[..gb_pos].trim_end(),
            all_cols.join(", "),
            sql[end_pos..].trim_start()
        ).trim().to_string()
    } else {
        let group = grouping.join(", ");
        format!("{sql} GROUP BY {group}")
    }
}

/// Build ORDER BY clause.
///
/// Mirrors `SQLParser.order_by()` from sql.pyx.
#[pyfunction]
#[pyo3(signature = (sql, ordering))]
pub fn order_by(sql: &str, ordering: Vec<String>) -> String {
    if ordering.is_empty() {
        return sql.to_string();
    }
    let order = ordering.join(", ");
    format!("{sql} ORDER BY {order}")
}

/// Build LIMIT/OFFSET clause.
///
/// Mirrors `SQLParser.limiting()` from sql.pyx.
#[pyfunction]
#[pyo3(signature = (sql, limit="", offset=""))]
pub fn limiting(sql: &str, limit: &str, offset: &str) -> String {
    let mut result = sql.to_string();

    // Handle {limit} placeholder
    if result.contains("{limit}") {
        let limit_clause = if !limit.is_empty() {
            format!("LIMIT {limit}")
        } else {
            String::new()
        };
        let mut m = HashMap::new();
        m.insert("limit".to_string(), limit_clause);
        result = safe_format_map_rust(&result, &m);
    } else if !limit.is_empty() {
        result = format!("{result} LIMIT {limit}");
    }

    // Handle {offset} placeholder
    if result.contains("{offset}") {
        if !offset.is_empty() {
            let offset_clause = format!("OFFSET {offset}");
            let mut m = HashMap::new();
            m.insert("offset".to_string(), offset_clause);
            result = safe_format_map_rust(&result, &m);
        }
    } else if !offset.is_empty() {
        result = format!("{result} OFFSET {offset}");
    }

    result
}

/// Process and replace fields in a SQL query.
///
/// Mirrors `SQLParser.process_fields()` from sql.pyx.
#[pyfunction]
#[pyo3(signature = (sql, fields, add_fields=false, query_raw=""))]
pub fn process_fields(
    sql: &str,
    fields: Vec<String>,
    add_fields: bool,
    query_raw: &str,
) -> String {
    if !fields.is_empty() {
        if add_fields {
            // Extract existing fields between SELECT and FROM
            let upper = sql.to_uppercase();
            if let (Some(sel_pos), Some(from_pos)) = (find_keyword(&upper, "SELECT"), find_keyword(&upper, "FROM")) {
                let after_select = sel_pos + 6; // len("SELECT")
                let current_fields: Vec<&str> =
                    sql[after_select..from_pos].split(',').map(|f| f.trim()).collect();
                let mut all_fields: Vec<String> =
                    current_fields.iter().map(|s| s.to_string()).collect();
                all_fields.extend(fields);
                return format!(
                    "{} {} {}",
                    &sql[..after_select],
                    all_fields.join(", "),
                    &sql[from_pos..]
                );
            }
        }
        let mut result = sql.replace(" * FROM", " {fields} FROM");
        let field_str = fields.join(", ");
        let mut m = HashMap::new();
        m.insert("fields".to_string(), field_str);
        result = safe_format_map_rust(&result, &m);
        return result;
    }

    // Check if {fields} is in the raw query but fields list is empty
    if query_raw.contains("{fields}") {
        return sql.to_string();
    }

    sql.to_string()
}

// ---------------------------------------------------------------------------
// build_sql — Parallel orchestrator via rayon
// ---------------------------------------------------------------------------

/// Build a complete SQL query by orchestrating all clause builders.
///
/// Uses `rayon::join` to process independent operations in parallel:
/// - Branch A: process_fields
/// - Branch B: group_by + order_by
///
/// filter_conditions is called from Python because it needs PyDict.
/// After fields, grouping, and ordering are done, limiting is applied.
#[pyfunction]
#[pyo3(signature = (sql, fields, add_fields, grouping, ordering, limit, offset, query_raw, conditions))]
pub fn build_sql(
    sql: &str,
    fields: Vec<String>,
    add_fields: bool,
    grouping: Vec<String>,
    ordering: Vec<String>,
    limit: &str,
    offset: &str,
    query_raw: &str,
    conditions: &Bound<'_, PyDict>,
) -> PyResult<String> {
    // Step 1: Process fields (can run in parallel with group/order prep)
    let (fields_result, (group_cols, order_cols)) = rayon::join(
        || process_fields(sql, fields, add_fields, query_raw),
        || {
            // Prepare group and order strings (cheap, but demonstrates the pattern)
            let g = grouping;
            let o = ordering;
            (g, o)
        },
    );

    // Step 2: Apply GROUP BY
    let result = group_by(&fields_result, group_cols);

    // Step 3: Apply ORDER BY
    let result = if !order_cols.is_empty() {
        order_by(&result, order_cols)
    } else {
        result
    };

    // Step 4: Apply LIMIT/OFFSET
    let result = limiting(&result, limit, offset);

    // Step 5: Apply remaining conditions via safe_format_map
    let mut cond_map: HashMap<String, String> = HashMap::new();
    for (k, v) in conditions.iter() {
        if let (Ok(key), Ok(val)) = (k.extract::<String>(), v.extract::<String>()) {
            cond_map.insert(key, val);
        }
    }

    let result = if !cond_map.is_empty() {
        safe_format_map_rust(&result, &cond_map)
    } else {
        result
    };

    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_build_string_condition_basic() {
        let mut conds = Vec::new();
        build_string_condition("name", "John", "name", "", &mut conds);
        assert_eq!(conds[0], "name='John'");
    }

    #[test]
    fn test_build_string_condition_null() {
        let mut conds = Vec::new();
        build_string_condition("name", "null", "name", "", &mut conds);
        assert_eq!(conds[0], "name IS NULL");
    }

    #[test]
    fn test_build_string_condition_not_null() {
        let mut conds = Vec::new();
        build_string_condition("name", "!null", "name", "", &mut conds);
        assert_eq!(conds[0], "name IS NOT NULL");
    }

    #[test]
    fn test_build_string_condition_negation() {
        let mut conds = Vec::new();
        build_string_condition("status", "!active", "status", "", &mut conds);
        assert_eq!(conds[0], "status != 'active'");
    }

    #[test]
    fn test_build_string_condition_between() {
        let mut conds = Vec::new();
        build_string_condition(
            "date",
            "BETWEEN '2024-01-01' AND '2024-12-31'",
            "date",
            "",
            &mut conds,
        );
        assert_eq!(
            conds[0],
            "(date BETWEEN '2024-01-01' AND '2024-12-31')"
        );
    }

    #[test]
    fn test_build_string_condition_end_bang() {
        let mut conds = Vec::new();
        build_string_condition("status!", "active", "status", "!", &mut conds);
        assert_eq!(conds[0], "status != active");
    }

    #[test]
    fn test_apply_where_clause_with_filter() {
        let result = apply_where_clause(
            "SELECT * FROM t {filter}",
            &["a=1".to_string()],
        )
        .unwrap();
        assert_eq!(result, "SELECT * FROM t  WHERE a=1");
    }

    #[test]
    fn test_apply_where_clause_with_where_cond() {
        let result = apply_where_clause(
            "SELECT * FROM t {where_cond}",
            &["a=1".to_string()],
        )
        .unwrap();
        assert_eq!(result, "SELECT * FROM t  WHERE a=1");
    }

    #[test]
    fn test_apply_where_clause_no_placeholder() {
        let result = apply_where_clause(
            "SELECT * FROM t",
            &["a=1".to_string(), "b=2".to_string()],
        )
        .unwrap();
        assert_eq!(result, "SELECT * FROM t WHERE a=1 AND b=2");
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
        let result =
            apply_where_clause("SELECT * FROM t {filter}", &[]).unwrap();
        assert_eq!(result, "SELECT * FROM t ");
    }

    #[test]
    fn test_group_by_new() {
        let result = group_by("SELECT * FROM t", vec!["col1".to_string()]);
        assert_eq!(result, "SELECT * FROM t GROUP BY col1");
    }

    #[test]
    fn test_group_by_multiple() {
        let result = group_by(
            "SELECT * FROM t",
            vec!["col1".to_string(), "col2".to_string()],
        );
        assert_eq!(result, "SELECT * FROM t GROUP BY col1, col2");
    }

    #[test]
    fn test_group_by_empty() {
        let result = group_by("SELECT * FROM t", vec![]);
        assert_eq!(result, "SELECT * FROM t");
    }

    #[test]
    fn test_order_by() {
        let result =
            order_by("SELECT * FROM t", vec!["col1 ASC".to_string()]);
        assert_eq!(result, "SELECT * FROM t ORDER BY col1 ASC");
    }

    #[test]
    fn test_order_by_empty() {
        let result = order_by("SELECT * FROM t", vec![]);
        assert_eq!(result, "SELECT * FROM t");
    }

    #[test]
    fn test_limiting_basic() {
        let result = limiting("SELECT * FROM t", "10", "");
        assert_eq!(result, "SELECT * FROM t LIMIT 10");
    }

    #[test]
    fn test_limiting_with_offset() {
        let result = limiting("SELECT * FROM t", "10", "20");
        assert_eq!(result, "SELECT * FROM t LIMIT 10 OFFSET 20");
    }

    #[test]
    fn test_limiting_placeholder() {
        let result = limiting("SELECT * FROM t {limit}", "10", "");
        assert_eq!(result, "SELECT * FROM t LIMIT 10");
    }

    #[test]
    fn test_limiting_empty() {
        let result = limiting("SELECT * FROM t", "", "");
        assert_eq!(result, "SELECT * FROM t");
    }

    #[test]
    fn test_process_fields_list() {
        let result = process_fields(
            "SELECT * FROM t",
            vec!["a".to_string(), "b".to_string()],
            false,
            "",
        );
        assert_eq!(result, "SELECT a, b FROM t");
    }

    #[test]
    fn test_process_fields_with_placeholder() {
        let result = process_fields(
            "SELECT {fields} FROM t",
            vec!["x".to_string(), "y".to_string()],
            false,
            "",
        );
        assert_eq!(result, "SELECT x, y FROM t");
    }

    #[test]
    fn test_process_fields_empty() {
        let result = process_fields("SELECT * FROM t", vec![], false, "");
        assert_eq!(result, "SELECT * FROM t");
    }
}
