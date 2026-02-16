// Copyright (C) 2018-present Jesus Lara
//
// safe_dict.rs — Rust reimplementation of SafeDict.format_map()
// Replaces {key} placeholders in SQL templates, leaving unmatched ones intact.

use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::collections::HashMap;

/// Replace `{key}` placeholders in a template with values from a dict.
///
/// Unmatched placeholders are left as-is (like Python's `SafeDict`).
///
/// # Examples
/// ```
/// let result = safe_format_map("SELECT {fields} FROM {table} {filter}",
///     &[("fields", "a, b"), ("table", "t1")]);
/// assert_eq!(result, "SELECT a, b FROM t1 {filter}");
/// ```
#[pyfunction]
#[pyo3(signature = (template, replacements))]
pub fn safe_format_map(template: &str, replacements: &Bound<'_, PyDict>) -> String {
    let mut map: HashMap<String, String> = HashMap::new();
    for (key, value) in replacements.iter() {
        if let (Ok(k), Ok(v)) = (key.extract::<String>(), value.extract::<String>()) {
            map.insert(k, v);
        }
    }
    safe_format_map_rust(template, &map)
}

/// Pure Rust implementation of safe_format_map (no Python dependency).
///
/// Used internally by other Rust functions.
pub fn safe_format_map_rust(template: &str, replacements: &HashMap<String, String>) -> String {
    let mut result = String::with_capacity(template.len());
    let bytes = template.as_bytes();
    let len = bytes.len();
    let mut i = 0;

    while i < len {
        if bytes[i] == b'{' {
            // Look for closing brace
            if let Some(end) = template[i + 1..].find('}') {
                let key = &template[i + 1..i + 1 + end];
                // Only replace if key is a simple identifier (no nested braces, spaces, etc.)
                if !key.is_empty()
                    && !key.contains('{')
                    && key
                        .chars()
                        .all(|c| c.is_alphanumeric() || c == '_')
                {
                    if let Some(value) = replacements.get(key) {
                        result.push_str(value);
                        i += end + 2; // skip past '}'
                        continue;
                    }
                }
            }
            // No match — emit the '{' character literally
            result.push('{');
            i += 1;
        } else {
            result.push(bytes[i] as char);
            i += 1;
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_safe_format_map_basic() {
        let mut m = HashMap::new();
        m.insert("fields".to_string(), "a, b".to_string());
        m.insert("table".to_string(), "t1".to_string());

        let result =
            safe_format_map_rust("SELECT {fields} FROM {table} {filter}", &m);
        assert_eq!(result, "SELECT a, b FROM t1 {filter}");
    }

    #[test]
    fn test_safe_format_map_no_placeholders() {
        let m = HashMap::new();
        let result = safe_format_map_rust("SELECT * FROM t1", &m);
        assert_eq!(result, "SELECT * FROM t1");
    }

    #[test]
    fn test_safe_format_map_all_replaced() {
        let mut m = HashMap::new();
        m.insert("a".to_string(), "1".to_string());
        m.insert("b".to_string(), "2".to_string());
        let result = safe_format_map_rust("{a} + {b}", &m);
        assert_eq!(result, "1 + 2");
    }

    #[test]
    fn test_safe_format_map_empty_template() {
        let m = HashMap::new();
        assert_eq!(safe_format_map_rust("", &m), "");
    }

    #[test]
    fn test_safe_format_map_nested_braces_ignored() {
        let m = HashMap::new();
        let result = safe_format_map_rust("{{not_a_placeholder}}", &m);
        // {{ is not a valid key, so left as-is
        assert_eq!(result, "{{not_a_placeholder}}");
    }
}
