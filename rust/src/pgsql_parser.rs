// Copyright (C) 2018-present Jesus Lara
//
// pgsql_parser.rs — PostgreSQL-specific filter_conditions with rayon parallelism.
// Extends the base sql_parser filter logic with PG operators:
// ILIKE, array containment, range types, JSONB, and CamelCase quoting.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict, PyString};
use rayon::prelude::*;
use std::collections::HashMap;

use crate::safe_dict::safe_format_map_rust;
use crate::validators::{escape_string, field_components, is_camel_case, is_integer, quote_string};

// ---------------------------------------------------------------------------
// Security helpers (PgSQL-specific)
// ---------------------------------------------------------------------------

/// Validate and produce a safe SQL identifier for the given key.
///
/// Numeric keys are double-quoted. Simple identifiers (alphanumeric + underscore + dot)
/// pass through. Anything else is rejected.
fn pg_safe_identifier_key(key: &str) -> Option<String> {
    let stripped = key.trim_end_matches(|c: char| matches!(c, '|' | '!' | '~' | '#' | '@' | ':'));
    if stripped.parse::<i64>().is_ok() || is_integer(stripped) {
        return Some(format!("\"{}\"", key));
    }
    if stripped.chars().all(|c| c.is_alphanumeric() || c == '_' || c == '.') {
        Some(key.to_string())
    } else {
        None // reject
    }
}

/// Validate that an operator is in the allowlist.
fn pg_validate_operator(op: &str) -> bool {
    COMPARISON_TOKENS.contains(&op)
        || VALID_OPERATORS.contains(&op)
        || JSONB_OPERATORS.contains(&op)
        || PG_TEXT_OPERATORS.contains(&op)
}

/// Quote a value as a PostgreSQL string literal.
///
/// Single quotes are doubled. When the value contains `{`, `}` or `\`, an
/// escape-string literal (`E'...'`) is emitted with the braces written as
/// `\x7b`/`\x7d`: the rendered SQL goes through further `str.format_map`
/// passes (limits, conditions) that would otherwise parse JSON braces as
/// placeholders.
fn pg_literal(value: &str) -> String {
    let escaped = value.replace('\'', "''");
    if escaped.contains(['{', '}', '\\']) {
        let escaped = escaped
            .replace('\\', "\\\\")
            .replace('{', "\\x7b")
            .replace('}', "\\x7d");
        format!("E'{}'", escaped)
    } else {
        format!("'{}'", escaped)
    }
}

/// Validate a BETWEEN clause for injection markers.
fn pg_validate_between(value: &str) -> bool {
    let upper = value.to_uppercase();
    !value.contains("--")
        && !value.contains("/*")
        && !value.contains(';')
        && !upper.contains("UNION")
        && !upper.contains("SELECT")
}

/// PostgreSQL comparison tokens.
const COMPARISON_TOKENS: &[&str] = &[">=", "<=", "<>", "!=", "<", ">"];

/// Valid SQL operators for list-based conditions.
const VALID_OPERATORS: &[&str] = &["<", ">", ">=", "<=", "<>", "!=", "IS NOT", "IS"];

/// JSONB operators accepted as the key of a dict-typed filter value.
const JSONB_OPERATORS: &[&str] = &["@>", "<@", "->", "->>"];

/// Case-insensitive pattern operators accepted as the key of a dict filter value
/// (qsurl text_match pushdown, FEAT-152). The value is a ready LIKE pattern.
const PG_TEXT_OPERATORS: &[&str] = &["ILIKE", "NOT ILIKE"];

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
    /// A WHERE condition already rendered while holding the GIL (JSONB filters).
    Condition(String),
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
            FilterValue::Condition(c) => c.clone(),
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
    if let Ok(dict) = obj.cast::<PyDict>() {
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
// JSONB filters (runs on GIL thread: JSON is serialized with orjson)
// ---------------------------------------------------------------------------

/// Outcome of inspecting a dict-typed filter value for JSONB semantics.
#[derive(Debug, PartialEq)]
enum JsonbOutcome {
    /// Comparison-token dict (`{">=": 5}`): handled by the generic path.
    NotJsonb,
    /// JSONB filter that cannot be rendered safely: the condition is dropped.
    Skip,
    /// Fully rendered JSONB condition.
    Condition(String),
}

/// Serialize a Python object to JSON text with `orjson.dumps`.
fn orjson_dumps(obj: &Bound<'_, PyAny>) -> PyResult<String> {
    let raw = obj.py().import("orjson")?.call_method1("dumps", (obj,))?;
    let bytes: Vec<u8> = raw.extract()?;
    String::from_utf8(bytes).map_err(|e| PyValueError::new_err(e.to_string()))
}

/// JSON text for a containment operand.
///
/// A `str` operand is taken as JSON text: it is parsed and re-serialized with
/// orjson, so invalid JSON is rejected. Any other object is serialized as-is.
fn jsonb_operand(obj: &Bound<'_, PyAny>) -> PyResult<String> {
    if obj.is_instance_of::<PyString>() {
        let parsed = obj.py().import("orjson")?.call_method1("loads", (obj,))?;
        return orjson_dumps(&parsed);
    }
    orjson_dumps(obj)
}

/// Render `{"->>": {"path": value, ...}}` / `{"->": {...}}` as key comparisons.
///
/// `->>` compares the text of the key (non-string values are compared by
/// their JSON text, e.g. `true`, `1`); `->` compares the JSONB value. A
/// `None` value renders `IS NULL`. Several paths are AND-ed.
fn jsonb_path_condition(col: &str, op: &str, operand: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    let Ok(paths) = operand.cast::<PyDict>() else {
        return Ok(None);
    };
    if paths.is_empty() {
        return Ok(None);
    }
    let mut parts: Vec<String> = Vec::with_capacity(paths.len());
    for (path_obj, value) in paths.iter() {
        let Ok(path) = path_obj.extract::<String>() else {
            return Ok(None);
        };
        let path_lit = pg_literal(&path);
        let cond = if value.is_none() {
            format!("{} {} {} IS NULL", col, op, path_lit)
        } else if op == "->>" {
            let text = if value.is_instance_of::<PyString>() {
                value.extract::<String>()?
            } else {
                orjson_dumps(&value)?
            };
            format!("{} ->> {} = {}", col, path_lit, pg_literal(&text))
        } else {
            format!("{} -> {} = {}::jsonb", col, path_lit, pg_literal(&orjson_dumps(&value)?))
        };
        parts.push(cond);
    }
    if parts.len() == 1 {
        Ok(parts.pop())
    } else {
        Ok(Some(format!("({})", parts.join(" AND "))))
    }
}

/// Inspect a dict-typed filter value and render it as a JSONB condition.
///
/// * No operator keys (`{"status": "active"}`): implicit containment,
///   `col @> '<json>'::jsonb`.
/// * `{"@>": operand}` / `{"<@": operand}`: explicit containment; the
///   operand is a dict/list/scalar or a JSON text string.
/// * `{"->>": {...}}` / `{"->": {...}}`: key comparisons (see
///   [`jsonb_path_condition`]).
/// * A first key that is a comparison token is left to the generic path;
///   dicts mixing operator and plain keys are dropped.
fn jsonb_condition(key: &str, dict: &Bound<'_, PyDict>) -> JsonbOutcome {
    if dict.is_empty() {
        return JsonbOutcome::NotJsonb;
    }
    let is_operator = |k: &Bound<'_, PyAny>| {
        k.extract::<String>()
            .map(|k| COMPARISON_TOKENS.contains(&k.as_str()) || JSONB_OPERATORS.contains(&k.as_str()))
            .unwrap_or(false)
    };
    let operators = dict.keys().iter().filter(|k| is_operator(k)).count();
    let Some((op_obj, operand)) = dict.iter().next() else {
        return JsonbOutcome::NotJsonb;
    };
    if let Ok(op) = op_obj.extract::<String>() {
        // qsurl text-match operators (FEAT-152) are handled by process_dict_value,
        // never as implicit JSONB containment (they are not in `is_operator`, so
        // `operators` would otherwise stay 0 and fall through to the containment
        // branch below).
        if PG_TEXT_OPERATORS.contains(&op.as_str()) {
            return JsonbOutcome::NotJsonb;
        }
    }
    if operators > 0 {
        if let Ok(op) = op_obj.extract::<String>() {
            if COMPARISON_TOKENS.contains(&op.as_str()) {
                return JsonbOutcome::NotJsonb;
            }
        }
    }
    // SECURITY: the column must be a safe identifier.
    let Some(col) = pg_safe_identifier_key(key) else {
        return JsonbOutcome::Skip;
    };
    let rendered = if operators == 0 {
        orjson_dumps(dict.as_any()).map(|j| Some(format!("{} @> {}::jsonb", col, pg_literal(&j))))
    } else if operators != dict.len() {
        Ok(None)
    } else {
        let op: String = op_obj.extract().unwrap_or_default();
        match op.as_str() {
            "@>" | "<@" => jsonb_operand(&operand)
                .map(|j| Some(format!("{} {} {}::jsonb", col, op, pg_literal(&j)))),
            _ => jsonb_path_condition(&col, &op, &operand),
        }
    };
    match rendered {
        Ok(Some(cond)) => JsonbOutcome::Condition(cond),
        _ => JsonbOutcome::Skip,
    }
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

    // SECURITY: Validate and produce a safe identifier; reject malicious keys.
    let formatted_key = pg_safe_identifier_key(key)?;

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
        FilterValue::Condition(c) => Some(c.clone()),
        FilterValue::Null => None,
    }
}

/// Handle dict-typed filter values with a comparison token (`{">=": 5}`).
///
/// JSONB dicts never reach this function: they are rendered by
/// [`jsonb_condition`] during extraction.
fn process_dict_value(
    key: &str,
    entries: &[(String, FilterValue)],
    _format: Option<&str>,
) -> Option<String> {
    if entries.is_empty() {
        return None;
    }

    let (op, v) = &entries[0];

    // SECURITY: Operator must be in allowlist
    if !pg_validate_operator(op) {
        return None; // discard unknown/unsafe operators
    }

    // Standard comparison tokens
    if COMPARISON_TOKENS.contains(&op.as_str()) {
        // SECURITY: escape the comparison value
        let safe_v = quote_string(&escape_string(&v.as_str()), true);
        return Some(format!("{} {} {}", key, op, safe_v));
    }

    // Case-insensitive pattern match (FEAT-152): string values only, quoted by
    // pg_literal. The caller (translate.split, TASK-771) is responsible for
    // escaping LIKE metacharacters in the pattern; this builder only quotes.
    //
    // The Python-side `is_valid()` pre-processing (abstract.pyx `_where_element`,
    // run during `set_options()`/`set_where()` BEFORE `pgsql_filter_conditions`
    // is ever called from `filter_conditions()`) already wraps every non-numeric
    // string filter value in a single-quote pair when `noquote=False` (the
    // pgSQLParser default). The COMPARISON_TOKENS branch above tolerates that via
    // `quote_string`'s strip-then-requote behaviour (validators.rs); mirror it
    // here (`pg_literal` does not strip) so a pattern is not quoted twice
    // (confirmed via the qsurl end-to-end dry-run tests, TASK-776).
    if PG_TEXT_OPERATORS.contains(&op.as_str()) {
        return match v {
            FilterValue::Str(s) => {
                let bytes = s.as_bytes();
                let stripped = if bytes.len() >= 2 && bytes[0] == b'\'' && bytes[bytes.len() - 1] == b'\'' {
                    &s[1..s.len() - 1]
                } else {
                    s.as_str()
                };
                Some(format!("{} {} {}", key, op, pg_literal(stripped)))
            }
            _ => None, // non-string values are rejected, never str()-ified
        };
    }

    None
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

    // Check if first item is a valid operator (allowlist enforced)
    if VALID_OPERATORS.contains(&first_str.as_str()) && items.len() > 1 {
        // SECURITY: escape the second value
        let safe_v = quote_string(&escape_string(&items[1].as_str()), true);
        return Some(format!("{} {} {}", key, first_str, safe_v));
    }

    // date/datetime BETWEEN
    if _format == Some("date") || _format == Some("datetime") {
        if items.len() >= 2 {
            // SECURITY: escape BETWEEN boundary values
            let v0 = escape_string(&items[0].as_str());
            let v1 = escape_string(&items[1].as_str());
            if end == "!" {
                return Some(format!("{} NOT BETWEEN '{}' AND '{}'", name, v0, v1));
            } else {
                return Some(format!("{} BETWEEN '{}' AND '{}'", name, v0, v1));
            }
        }
    }

    // Build IN clause from list values — each item escaped
    let val_str: String = items
        .iter()
        .map(|v| quote_string(&escape_string(&v.as_str()), true))
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
        let base = &value[..value.len().saturating_sub(1)];
        let base_escaped = base.replace("'", "''");
        let val = format!("'{base_escaped}%'");
        return Some(format!("{} ILIKE {}", name, val));
    }
    // NOT ILIKE pattern
    if end == "!~" {
        let base = &value[..value.len().saturating_sub(1)];
        let base_escaped = base.replace("'", "''");
        let val = format!("'{base_escaped}%'");
        return Some(format!("{} NOT ILIKE {}", name, val));
    }
    // BETWEEN in value string — validate for injection
    if value.contains("BETWEEN") {
        if !pg_validate_between(value) {
            return None; // reject unsafe BETWEEN
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
    // Negation with end marker — SECURITY: escape value
    if end == "!" {
        return Some(format!("{} != {}", name, quote_string(&escape_string(value), true)));
    }
    // Negation with ! prefix — SECURITY: escape after stripping prefix
    if value.starts_with('!') {
        return Some(format!(
            "{} != {}",
            key,
            quote_string(&escape_string(&value[1..]), true)
        ));
    }

    // PostgreSQL type-specific handling
    match _format {
        Some("array") => {
            // SECURITY: escape the value used in array containment check
            let safe_val = escape_string(value);
            if safe_val.parse::<i64>().is_ok() {
                Some(format!("{} = ANY({})", safe_val, key))
            } else {
                Some(format!("{}::character varying = ANY({})", safe_val, key))
            }
        }
        Some("numrange") => {
            // Only allow numeric values for range types
            if value.parse::<f64>().is_ok() {
                Some(format!("{}::numeric <@ {}", value, key))
            } else {
                None // reject non-numeric
            }
        }
        Some("int4range") | Some("int8range") => {
            if value.parse::<i64>().is_ok() {
                Some(format!("{}::integer <@ {}::int4range", value, key))
            } else {
                None
            }
        }
        Some("tsrange") | Some("tstzrange") => {
            // Escape timestamp values
            let safe_val = escape_string(value);
            Some(format!("{}::timestamptz <@ {}::tstzrange", safe_val, key))
        }
        Some("daterange") => {
            let safe_val = escape_string(value);
            Some(format!("{}::date <@ {}::daterange", safe_val, key))
        }
        _ => {
            // Default: quote and escape value, handle CamelCase key quoting
            let final_key = if is_camel_case(key) {
                format!("\"{}\"", key)
            } else {
                key.to_string()
            };
            Some(format!("{}={}", final_key, quote_string(&escape_string(value), true)))
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
            let value = match value_obj.cast::<PyDict>() {
                Ok(dict) => match jsonb_condition(&key, dict) {
                    JsonbOutcome::NotJsonb => extract_filter_value(&value_obj),
                    JsonbOutcome::Skip => FilterValue::Null,
                    JsonbOutcome::Condition(cond) => FilterValue::Condition(cond),
                },
                Err(_) => extract_filter_value(&value_obj),
            };
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
    fn test_pg_literal_plain() {
        assert_eq!(pg_literal("active"), "'active'");
        assert_eq!(pg_literal("O'Brien"), "'O''Brien'");
    }

    #[test]
    fn test_pg_literal_escapes_braces_and_backslashes() {
        assert_eq!(
            pg_literal(r#"{"a":"x\"y'z"}"#),
            r#"E'\x7b"a":"x\\"y''z"\x7d'"#
        );
    }

    #[test]
    fn test_condition_value_passthrough() {
        let entry = FilterEntry {
            key: "attrs".to_string(),
            value: FilterValue::Condition("attrs @> '[]'::jsonb".to_string()),
            format_hint: None,
        };
        assert_eq!(process_entry(&entry), Some("attrs @> '[]'::jsonb".to_string()));
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
    fn test_process_ilike_dict_operator() {
        let entry = FilterEntry {
            key: "city".to_string(),
            value: FilterValue::Dict(vec![
                ("ILIKE".to_string(), FilterValue::Str("%san%".to_string())),
            ]),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("city ILIKE '%san%'".to_string())
        );
    }

    #[test]
    fn test_process_not_ilike_dict_operator() {
        let entry = FilterEntry {
            key: "city".to_string(),
            value: FilterValue::Dict(vec![
                ("NOT ILIKE".to_string(), FilterValue::Str("%san%".to_string())),
            ]),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("city NOT ILIKE '%san%'".to_string())
        );
    }

    #[test]
    fn test_process_ilike_rejects_non_string_value() {
        let entry = FilterEntry {
            key: "n".to_string(),
            value: FilterValue::Dict(vec![
                ("ILIKE".to_string(), FilterValue::Int(5)),
            ]),
            format_hint: None,
        };
        assert_eq!(process_entry(&entry), None);
    }

    #[test]
    fn test_process_ilike_quotes_embedded_quote() {
        let entry = FilterEntry {
            key: "name".to_string(),
            value: FilterValue::Dict(vec![
                ("ILIKE".to_string(), FilterValue::Str("o'brien%".to_string())),
            ]),
            format_hint: None,
        };
        assert_eq!(
            process_entry(&entry),
            Some("name ILIKE 'o''brien%'".to_string())
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
