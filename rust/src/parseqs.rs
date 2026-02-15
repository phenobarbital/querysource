// Copyright (C) 2018-present Jesus Lara
//
// parseqs.rs — Rust reimplementation of querysource/utils/parseqs.pyx
// Prefix-based parsing for query string values: lists, tuples, dicts.

use pyo3::prelude::*;

/// Determine if a string value is parseable as a list, tuple, or dict.
///
/// Returns a string tag: "list", "tuple", "dict", or empty string if not parseable.
#[pyfunction]
#[pyo3(signature = (value,))]
pub fn is_parseable(value: &str) -> &'static str {
    if value.is_empty() {
        return "";
    }
    match value.as_bytes()[0] {
        b'[' => "list",
        b'(' => "tuple",
        b'{' => "dict",
        _ => "",
    }
}

/// Parse a string enclosed in brackets as a list of trimmed strings.
///
/// Input: `"[a, b, c]"` → Output: `["a", "b", "c"]`
#[pyfunction]
#[pyo3(signature = (value,))]
pub fn parse_list(value: &str) -> Vec<String> {
    parse_enclosed(value, '[', ']')
}

/// Parse a string enclosed in parentheses as a list of trimmed strings.
///
/// Input: `"(a, b, c)"` → Output: `["a", "b", "c"]`
#[pyfunction]
#[pyo3(signature = (value,))]
pub fn parse_tuple(value: &str) -> Vec<String> {
    parse_enclosed(value, '(', ')')
}

/// Strip enclosing delimiters and split by comma.
fn parse_enclosed(value: &str, open: char, close: char) -> Vec<String> {
    let trimmed = value.trim();
    let inner = if trimmed.starts_with(open) && trimmed.ends_with(close) {
        &trimmed[1..trimmed.len() - 1]
    } else {
        trimmed
    };

    inner
        .split(',')
        .map(|s| {
            let t = s.trim();
            // Strip surrounding quotes if present
            if (t.starts_with('\'') && t.ends_with('\''))
                || (t.starts_with('"') && t.ends_with('"'))
            {
                t[1..t.len() - 1].to_string()
            } else {
                t.to_string()
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_parseable() {
        assert_eq!(is_parseable("[1,2,3]"), "list");
        assert_eq!(is_parseable("(1,2,3)"), "tuple");
        assert_eq!(is_parseable("{\"a\":1}"), "dict");
        assert_eq!(is_parseable("hello"), "");
        assert_eq!(is_parseable(""), "");
    }

    #[test]
    fn test_parse_list() {
        let result = parse_list("[a, b, c]");
        assert_eq!(result, vec!["a", "b", "c"]);
    }

    #[test]
    fn test_parse_list_quoted() {
        let result = parse_list("['hello', 'world']");
        assert_eq!(result, vec!["hello", "world"]);
    }

    #[test]
    fn test_parse_tuple() {
        let result = parse_tuple("(x, y, z)");
        assert_eq!(result, vec!["x", "y", "z"]);
    }
}
