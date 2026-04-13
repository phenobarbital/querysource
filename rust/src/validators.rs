// Copyright (C) 2018-present Jesus Lara
//
// validators.rs — Rust reimplementation of querysource/types/validators.pyx
// Pure string/data processing functions for SQL query building.

use once_cell::sync::Lazy;
use pyo3::prelude::*;
use regex::Regex;

// Regex for field_components: ^(?:(@|!|#|~|:|))(\w*)(?:(\||\&|\!|\~|\#)|)+$
static EVAL_FIELD: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^(?:(@|!|#|~|:|))(\w*)(?:(\||\&|\!|\~|\#)|)+$").unwrap()
});

/// Regex for CamelCase detection.
static CAMEL_CASE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^(?:[A-Z][a-z]+)+$").unwrap()
});

/// UDF names recognized by the query system.
static UDF_LIST: Lazy<Vec<&str>> = Lazy::new(|| {
    vec![
        "CURRENT_YEAR",
        "CURRENT_MONTH",
        "TODAY",
        "YESTERDAY",
        "LAST_YEAR",
        "FDOM",
        "LDOM",
    ]
});

/// PostgreSQL constant names.
static PG_CONSTANTS: Lazy<Vec<&str>> =
    Lazy::new(|| vec!["CURRENT_DATE", "CURRENT_TIMESTAMP"]);

// ---------------------------------------------------------------------------
// Type checkers
// ---------------------------------------------------------------------------

/// Convert a string representation of truth to bool.
#[pyfunction]
#[pyo3(signature = (val,))]
pub fn strtobool(val: &str) -> PyResult<bool> {
    match val.to_lowercase().as_str() {
        "y" | "yes" | "t" | "true" | "on" | "1" => Ok(true),
        "n" | "no" | "f" | "false" | "off" | "0" | "null" => Ok(false),
        _ => Err(pyo3::exceptions::PyValueError::new_err(format!(
            "invalid truth value for {val}"
        ))),
    }
}

/// Check if a value string looks like an integer.
#[pyfunction]
#[pyo3(signature = (value,))]
pub fn is_integer(value: &str) -> bool {
    value.parse::<i64>().is_ok()
}

/// Check if a value string looks like a float.
#[pyfunction]
#[pyo3(signature = (value,))]
pub fn is_float(value: &str) -> bool {
    value.parse::<f64>().is_ok()
}

/// Check if a value string looks like a boolean.
#[pyfunction]
#[pyo3(signature = (value,))]
pub fn is_boolean(value: &str) -> bool {
    strtobool(value).is_ok()
}

/// Check if a value string is a known UDF.
#[pyfunction]
#[pyo3(signature = (value,))]
pub fn is_udf(value: &str) -> bool {
    UDF_LIST.contains(&value.to_uppercase().as_str())
}

/// Check if a value string is a known PG constant.
#[pyfunction]
#[pyo3(signature = (value,))]
pub fn is_pgconstant(value: &str) -> bool {
    PG_CONSTANTS.contains(&value.to_uppercase().as_str())
}

/// Check if value looks like a PG function call (contains parentheses or is `now()`).
#[pyfunction]
#[pyo3(signature = (value,))]
pub fn is_pg_function(value: &str) -> bool {
    value.contains('(') || value == "now()"
}

// ---------------------------------------------------------------------------
// String processing
// ---------------------------------------------------------------------------

/// Check if a string is CamelCase or contains spaces.
///
/// Mirrors `is_camel_case()` from validators.pyx.
#[pyfunction]
#[pyo3(signature = (value,))]
pub fn is_camel_case(value: &str) -> bool {
    if value.contains(' ') {
        return true;
    }
    CAMEL_CASE.is_match(value)
}

/// Parse a field string into (prefix, name, suffix) components.
///
/// Mirrors the Cython `field_components()` which uses regex:
/// `r'^(?:(@|!|#|~|:|))(\w*)(?:(\||\&|\!|\~|\#)|)+$'`
///
/// Returns a list of tuples `[(prefix, name, suffix), ...]`.
#[pyfunction]
#[pyo3(signature = (field,))]
pub fn field_components(field: &str) -> Vec<(String, String, String)> {
    EVAL_FIELD
        .captures_iter(field)
        .map(|cap| {
            (
                cap.get(1).map_or("", |m| m.as_str()).to_string(),
                cap.get(2).map_or("", |m| m.as_str()).to_string(),
                cap.get(3).map_or("", |m| m.as_str()).to_string(),
            )
        })
        .collect()
}

/// Escape special characters in a string for SQL safety.
///
/// Mirrors `escape_string()` from validators.pyx.
#[pyfunction]
#[pyo3(signature = (value,))]
pub fn escape_string(value: &str) -> String {
    let mut result = String::with_capacity(value.len());
    for ch in value.chars() {
        match ch {
            '\0' => result.push_str("\\0"),
            '\r' => result.push_str("\\r"),
            '\x08' => result.push_str("\\b"),
            '\x09' => result.push_str("\\t"),
            '\x1a' => result.push_str("\\z"),
            '\n' => result.push_str("\\n"),
            '"' | '\'' => {} // strip quotes
            '\\' => result.push_str("\\\\"),
            '%' => result.push_str("\\%"),
            _ => result.push(ch),
        }
    }
    result
}

/// Quote a string value for SQL queries.
///
/// Mirrors `Entity.quoteString()` from validators.pyx.
/// Handles None-like strings, null, booleans, double-quote conversion,
/// and proper single-quote escaping.
#[pyfunction]
#[pyo3(signature = (value, no_dblquoting=true))]
pub fn quote_string(value: &str, no_dblquoting: bool) -> String {
    if value == "None" {
        return "''".to_string();
    }
    if value == "null" || value == "NULL" {
        return value.to_string();
    }
    // boolean detection
    if value == "True" || value == "true" || value == "False" || value == "false" {
        return value.to_string();
    }

    let mut v = value.to_string();

    // Handle double quotes → single quotes
    if v.starts_with('"') && no_dblquoting {
        v = v.replace('"', "'");
    }

    let start_quote = v.starts_with('\'');
    let end_quote = v.ends_with('\'');

    // Strip existing quotes
    let inner = if start_quote && end_quote && v.len() >= 2 {
        &v[1..v.len() - 1]
    } else if start_quote {
        &v[1..]
    } else if end_quote {
        &v[..v.len() - 1]
    } else {
        &v
    };

    // Escape internal single quotes
    let escaped = inner.replace('\'', "''");

    format!("'{escaped}'")
}

/// Quote a string value for BigQuery using double-quote delimiters.
///
/// BigQuery strings enclosed in double quotes do not require single-quote
/// escaping, so "Sam's Club" is valid as-is.  Internal double quotes are
/// backslash-escaped.  Null sentinel values are passed through unquoted.
pub fn bq_quote_string(value: &str) -> String {
    if value == "null" || value == "NULL" {
        return value.to_string();
    }
    let v = value.replace('"', "\\\"");
    format!("\"{}\"", v)
}

/// Escape and quote a string — combines escape_string + quoteString.
///
/// Mirrors `to_string()` from validators.pyx.
#[pyfunction]
#[pyo3(signature = (value,))]
pub fn to_string(value: &str) -> String {
    quote_string(&escape_string(value), true)
}

/// Convert value to unquoted integer string (or return as-is if already numeric).
pub fn to_unquoted(value: &str) -> String {
    match value.parse::<i64>() {
        Ok(n) => n.to_string(),
        Err(_) => value.to_string(),
    }
}

// ---------------------------------------------------------------------------
// is_valid — Type dispatch & validation
// ---------------------------------------------------------------------------

/// Validate and convert a value based on its type hint.
///
/// Mirrors `is_valid()` from validators.pyx.
/// Returns the properly formatted value for SQL inclusion.
///
/// Type dispatch:
/// - `literal` → escape_string
/// - `integer`/`int`/`float`/`numeric`/`decimal` → unquoted
/// - `string`/`varchar`/`field` → quote_string(escape_string(v))
/// - `boolean` → TRUE/FALSE
/// - `date`/`datetime`/`timestamp` → quote_string
/// - `uuid` → quote_string
/// - null-like → `null`
/// - UDF → resolve and quote
/// - PG constant → UPPER
///
/// Falls back to quote_string for unknown types.
#[pyfunction]
#[pyo3(signature = (key, value, type_hint=None, noquote=false))]
#[allow(unused_variables)]
pub fn is_valid(
    key: &str,
    value: &str,
    type_hint: Option<&str>,
    noquote: bool,
) -> String {
    // Type hint dispatch
    if let Some(t) = type_hint {
        match t {
            "literal" => return escape_string(value),
            "int" | "integer" | "float" | "numeric" | "decimal" | "epoch" => {
                return to_unquoted(value);
            }
            "boolean" => {
                if let Ok(b) = strtobool(value) {
                    return if b {
                        "TRUE".to_string()
                    } else {
                        "FALSE".to_string()
                    };
                }
            }
            "string" | "varchar" | "field" => {
                return to_string(value);
            }
            "date" | "datetime" | "timestamp" => {
                return quote_string(value, true);
            }
            "uuid" => {
                return quote_string(value, true);
            }
            "array" | "json" => {
                return to_unquoted(value);
            }
            _ => {} // fall through to generic logic
        }
    }

    // Generic logic (no type hint or unrecognized type)
    if value == "null" || value == "NULL" || value == "None" || value.is_empty() {
        return "null".to_string();
    }
    // Boolean check
    if is_boolean(value) {
        return value.to_string();
    }
    // Integer check
    if is_integer(value) {
        return value.to_string();
    }
    // UDF check
    let upper = value.to_uppercase();
    if is_udf(&upper) {
        // In Rust we can't call the UDF, but we return the value
        // which the Python layer will resolve
        if noquote {
            return value.to_string();
        }
        return quote_string(value, true);
    }
    // PG constant
    if is_pgconstant(&upper) {
        return upper;
    }
    // PG function
    if is_pg_function(value) {
        return value.to_string();
    }

    // Default: quote
    if noquote {
        value.to_string()
    } else {
        quote_string(value, true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_strtobool() {
        assert!(strtobool("true").unwrap());
        assert!(strtobool("YES").unwrap());
        assert!(strtobool("1").unwrap());
        assert!(!strtobool("false").unwrap());
        assert!(!strtobool("no").unwrap());
        assert!(!strtobool("0").unwrap());
        assert!(strtobool("maybe").is_err());
    }

    #[test]
    fn test_field_components() {
        let result = field_components("@my_var!");
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, "@");
        assert_eq!(result[0].1, "my_var");
        assert_eq!(result[0].2, "!");
    }

    #[test]
    fn test_field_components_no_prefix() {
        let result = field_components("plain_field");
        // No prefix/suffix match — returns empty
        assert!(result.is_empty());
    }

    #[test]
    fn test_quote_string_basic() {
        assert_eq!(quote_string("hello", true), "'hello'");
    }

    #[test]
    fn test_quote_string_already_quoted() {
        assert_eq!(quote_string("'hello'", true), "'hello'");
    }

    #[test]
    fn test_quote_string_internal_quotes() {
        assert_eq!(quote_string("it's", true), "'it''s'");
    }

    #[test]
    fn test_quote_string_null() {
        assert_eq!(quote_string("null", true), "null");
        assert_eq!(quote_string("NULL", true), "NULL");
    }

    #[test]
    fn test_quote_string_none() {
        assert_eq!(quote_string("None", true), "''");
    }

    #[test]
    fn test_escape_string() {
        assert_eq!(escape_string("hello\nworld"), "hello\\nworld");
        assert_eq!(escape_string("it's"), "its");
        assert_eq!(escape_string("100%"), "100\\%");
    }

    #[test]
    fn test_is_valid_integer() {
        assert_eq!(is_valid("x", "42", Some("integer"), false), "42");
    }

    #[test]
    fn test_is_valid_string() {
        assert_eq!(is_valid("x", "hello", Some("string"), false), "'hello'");
    }

    #[test]
    fn test_is_valid_null() {
        assert_eq!(is_valid("x", "null", None, false), "null");
        assert_eq!(is_valid("x", "NULL", None, false), "null");
    }

    #[test]
    fn test_is_valid_boolean() {
        assert_eq!(is_valid("x", "true", Some("boolean"), false), "TRUE");
        assert_eq!(is_valid("x", "false", Some("boolean"), false), "FALSE");
    }

    #[test]
    fn test_is_valid_pg_constant() {
        assert_eq!(
            is_valid("x", "CURRENT_DATE", None, false),
            "CURRENT_DATE"
        );
    }

    #[test]
    fn test_is_valid_pg_function() {
        assert_eq!(is_valid("x", "now()", None, false), "now()");
    }

    #[test]
    fn test_is_valid_noquote() {
        assert_eq!(is_valid("x", "some_value", None, true), "some_value");
    }

    #[test]
    fn test_is_valid_default_quote() {
        assert_eq!(
            is_valid("x", "some_value", None, false),
            "'some_value'"
        );
    }
}
