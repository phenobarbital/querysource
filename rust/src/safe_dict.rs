// Copyright (C) 2018-present Jesus Lara
//
// safe_dict.rs — Rust reimplementation of SafeDict.format_map()
// Replaces {key} placeholders in SQL templates, leaving unmatched ones intact.

use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::collections::HashMap;

use crate::validators::{escape_string, is_valid};

// ---------------------------------------------------------------------------
// Injection detection helpers
// ---------------------------------------------------------------------------

/// SQL injection marker patterns that must always be rejected in user-supplied values.
const INJECTION_MARKERS: &[&str] = &[
    "--",   // line comment
    "/*",   // block comment start
    "*/",   // block comment end
    ";",    // statement separator
    "\\x",  // hex escape
    "\\u",  // unicode escape
];

/// SQL keywords that should not appear as plain scalar values.
const SQL_KEYWORDS: &[&str] = &[
    "UNION", "SELECT", "INSERT", "UPDATE", "DELETE", "DROP", "CREATE",
    "ALTER", "EXEC", "EXECUTE", "TRUNCATE", "GRANT", "REVOKE",
    "INFORMATION_SCHEMA", "PG_DATABASE", "PG_USER", "PG_SHADOW",
    "PG_ROLES", "PG_CATALOG", "AUTH_USER",
];

/// Placeholder context: what character immediately precedes/follows the `{...}`.
#[derive(Debug, PartialEq, Clone, Copy)]
enum PlaceholderContext {
    /// `'{key}'` — value is inside single-quoted SQL literal; emit escaped inner value.
    Literal,
    /// `"{key}"` — value is inside double-quoted SQL identifier; emit escaped identifier.
    Identifier,
    /// `{key}` — bare placeholder; emit fully quoted token via `quote_string`.
    Bare,
}

/// Check whether a value contains SQL injection markers.
///
/// Returns `Err` with a rejection reason if the value should be rejected.
fn check_injection(value: &str) -> Result<(), String> {
    let upper = value.to_uppercase();
    for marker in INJECTION_MARKERS {
        if value.contains(marker) {
            return Err(format!("Injection marker detected in value"));
        }
    }
    for kw in SQL_KEYWORDS {
        // Reject if value *starts with* a SQL keyword (common attack pattern)
        if upper.starts_with(kw) || upper.contains(&format!(" {} ", kw)) {
            return Err(format!("SQL keyword detected in value"));
        }
    }
    Ok(())
}

/// Check whether a key (identifier) is safe to use.
///
/// Valid identifiers: alphanumeric + underscore only, no quotes or special chars.
fn check_identifier_key(key: &str) -> Result<(), String> {
    if key.chars().all(|c| c.is_alphanumeric() || c == '_') {
        Ok(())
    } else {
        Err(format!("Invalid identifier key"))
    }
}

/// Detect the placeholder context by inspecting the template bytes around `{key}`.
///
/// `before_char` is the byte immediately before `{` (or 0 if at start).
/// `after_char` is the byte immediately after `}` (or 0 if at end).
fn detect_context(before_char: u8, after_char: u8) -> PlaceholderContext {
    if before_char == b'\'' && after_char == b'\'' {
        PlaceholderContext::Literal
    } else if before_char == b'"' && after_char == b'"' {
        PlaceholderContext::Identifier
    } else {
        PlaceholderContext::Bare
    }
}

/// Context-aware validating substitution (pure Rust, no Python dependency).
///
/// Walks `template`, finds each `{key}` placeholder, detects its surrounding context,
/// validates and escapes the value, then substitutes. Rejects injection-shaped values.
///
/// - `Literal` context (`'{key}'`): emit escaped inner value (quotes already in template).
/// - `Identifier` context (`"{key}"`): emit escaped identifier body (quotes already in template).
/// - `Bare` context (`{key}`): emit fully quoted token.
///
/// Returns `Err` if any value fails validation.
pub fn safe_format_map_validated_rust(
    template: &str,
    conditions: &HashMap<String, String>,
    cond_definition: &HashMap<String, String>,
) -> Result<String, String> {
    let mut result = String::with_capacity(template.len() + 64);
    let bytes = template.as_bytes();
    let len = bytes.len();
    let mut i = 0;

    while i < len {
        if bytes[i] == b'{' {
            // Find closing brace
            if let Some(end_offset) = template[i + 1..].find('}') {
                let key = &template[i + 1..i + 1 + end_offset];

                // Only substitute simple identifiers (same rule as safe_format_map_rust)
                if !key.is_empty()
                    && !key.contains('{')
                    && key.chars().all(|c| c.is_alphanumeric() || c == '_')
                {
                    if let Some(value) = conditions.get(key) {
                        // Detect context from surrounding chars
                        let before_char = if i > 0 { bytes[i - 1] } else { 0 };
                        let after_pos = i + 1 + end_offset + 1;
                        let after_char = if after_pos < len { bytes[after_pos] } else { 0 };
                        let ctx = detect_context(before_char, after_char);

                        // Validate the key
                        check_identifier_key(key)?;

                        // Validate the value against injection
                        check_injection(value)?;

                        // Get type hint from cond_definition (sparse is OK)
                        let type_hint = cond_definition.get(key).map(|s| s.as_str());

                        // Escape/quote based on context
                        let escaped = match ctx {
                            PlaceholderContext::Literal => {
                                // Template already has surrounding `'...'`
                                // Emit the escaped inner value (no extra quotes)
                                escape_string(value)
                            }
                            PlaceholderContext::Identifier => {
                                // Template already has surrounding `"..."`
                                // Emit an escaped identifier body (no extra double-quotes)
                                // An identifier with double-quote breakout must be rejected
                                if value.contains('"') || value.contains('\\') {
                                    return Err(format!("Identifier breakout attempt detected"));
                                }
                                // Extra check: reject SQL keywords in identifier position
                                let upper = value.to_uppercase();
                                for kw in SQL_KEYWORDS {
                                    if upper.contains(kw) {
                                        return Err(format!("SQL keyword in identifier context"));
                                    }
                                }
                                value.clone()
                            }
                            PlaceholderContext::Bare => {
                                // No surrounding quotes — emit a fully quoted token
                                is_valid(key, value, type_hint, false)
                            }
                        };

                        result.push_str(&escaped);
                        i += end_offset + 2; // skip past `}`
                        continue;
                    }
                }
            }
            // No match — leave `{` as-is (same as safe_format_map_rust)
            result.push('{');
            i += 1;
        } else {
            result.push(bytes[i] as char);
            i += 1;
        }
    }

    Ok(result)
}

/// Context-aware validating substitution exposed to Python via PyO3.
///
/// Walks `template` and substitutes each `{key}` placeholder with the
/// corresponding value from `conditions`, applying context-aware escaping
/// and rejecting injection-shaped inputs.
///
/// # Arguments
/// * `template`       — SQL template string (may contain `{key}` placeholders)
/// * `conditions`     — request-derived substitution map (untrusted)
/// * `cond_definition`— per-placeholder type hints (may be sparse; used by `is_valid`)
///
/// # Raises
/// * `PyValueError`  — if any value fails validation (injection detected)
#[pyfunction]
#[pyo3(signature = (template, conditions, cond_definition))]
pub fn safe_format_map_validated(
    template: &str,
    conditions: &Bound<'_, PyDict>,
    cond_definition: &Bound<'_, PyDict>,
) -> PyResult<String> {
    // Extract Python dicts to Rust HashMaps
    let mut conds: HashMap<String, String> = HashMap::new();
    for (k, v) in conditions.iter() {
        if let (Ok(key), Ok(val)) = (k.extract::<String>(), v.extract::<String>()) {
            conds.insert(key, val);
        }
    }
    let mut cond_def: HashMap<String, String> = HashMap::new();
    for (k, v) in cond_definition.iter() {
        if let (Ok(key), Ok(val)) = (k.extract::<String>(), v.extract::<String>()) {
            cond_def.insert(key, val);
        }
    }

    safe_format_map_validated_rust(template, &conds, &cond_def)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e))
}

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

    fn map(pairs: &[(&str, &str)]) -> HashMap<String, String> {
        pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect()
    }

    // --- safe_format_map_rust tests (unchanged behavior) ---

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

    /// safe_format_map must still do plain, no-escape substitution (TASK-704 contract).
    #[test]
    fn safe_format_map_unchanged() {
        let m = map(&[("filter", "WHERE x = 'a'")]);
        assert_eq!(
            safe_format_map_rust("SELECT * FROM t {filter}", &m),
            "SELECT * FROM t WHERE x = 'a'"
        );
    }

    // --- safe_format_map_validated_rust tests ---

    /// PoC: identifier breakout via `"` in an identifier context.
    #[test]
    fn rejects_identifier_breakout() {
        let r = safe_format_map_validated_rust(
            "SELECT a FROM t WHERE \"{k}\" = 1",
            &map(&[("k", "client_slug\" IS NULL UNION SELECT version()--")]),
            &map(&[]),
        );
        assert!(r.is_err(), "Expected rejection of identifier breakout");
    }

    /// PoC: UNION SELECT in a literal context.
    #[test]
    fn rejects_union_payload_literal() {
        let r = safe_format_map_validated_rust(
            "SELECT a FROM t WHERE client_slug = '{client_slug}'",
            &map(&[("client_slug", "' UNION SELECT version(),null,null--")]),
            &map(&[]),
        );
        assert!(r.is_err(), "Expected rejection of UNION SELECT payload");
    }

    /// PoC: pg_database enumeration payload.
    #[test]
    fn rejects_pg_database_payload() {
        let r = safe_format_map_validated_rust(
            "WHERE slug = '{slug}'",
            &map(&[("slug", "' UNION SELECT string_agg(datname,',') FROM pg_database--")]),
            &map(&[]),
        );
        assert!(r.is_err(), "Expected rejection of pg_database enumeration");
    }

    /// Benign literal value passes and renders correctly.
    #[test]
    fn allows_benign_literal() {
        let r = safe_format_map_validated_rust(
            "WHERE client_slug = '{client_slug}'",
            &map(&[("client_slug", "acme")]),
            &map(&[]),
        )
        .unwrap();
        assert_eq!(r, "WHERE client_slug = 'acme'");
    }

    /// Benign identifier key passes and value is emitted unquoted inside the `"..."`.
    #[test]
    fn allows_benign_identifier() {
        let r = safe_format_map_validated_rust(
            "SELECT 1 WHERE \"{col}\" = 1",
            &map(&[("col", "client_slug")]),
            &map(&[]),
        )
        .unwrap();
        assert_eq!(r, "SELECT 1 WHERE \"client_slug\" = 1");
    }

    /// Bare context: value is fully quoted.
    #[test]
    fn allows_bare_context_quoted() {
        let r = safe_format_map_validated_rust(
            "WHERE x = {val}",
            &map(&[("val", "hello")]),
            &map(&[]),
        )
        .unwrap();
        assert_eq!(r, "WHERE x = 'hello'");
    }

    /// Comment marker `--` in value is rejected.
    #[test]
    fn rejects_comment_marker() {
        let r = safe_format_map_validated_rust(
            "WHERE x = '{x}'",
            &map(&[("x", "1'--")]),
            &map(&[]),
        );
        assert!(r.is_err(), "Expected rejection of comment marker");
    }

    /// Statement separator `;` in value is rejected.
    #[test]
    fn rejects_statement_separator() {
        let r = safe_format_map_validated_rust(
            "WHERE x = '{x}'",
            &map(&[("x", "1; DROP TABLE users")]),
            &map(&[]),
        );
        assert!(r.is_err(), "Expected rejection of statement separator");
    }

    /// Unmatched placeholder is left intact (same as safe_format_map_rust).
    #[test]
    fn unmatched_placeholder_left_intact() {
        let r = safe_format_map_validated_rust(
            "SELECT * FROM t {unmatched}",
            &map(&[]),
            &map(&[]),
        )
        .unwrap();
        assert_eq!(r, "SELECT * FROM t {unmatched}");
    }
}
