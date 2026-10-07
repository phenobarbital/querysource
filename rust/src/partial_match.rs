// partial_match.rs — FEAT-180 operator table shared by the Rust WHERE builders.
// Twin of querysource/parsers/partial_matching.py: same 20 rows, same order, same messages.
#![allow(dead_code)]

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use regex::Regex;
use std::sync::LazyLock;

pub const CONTAINS_MIN_LENGTH: usize = 3;
pub const MAX_REGEX_PATTERN_LENGTH: usize = 200;

static NESTED_QUANTIFIER_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\([^()]*[+*][^()]*\)[+*]").unwrap());

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MatchKind { Like, Regex }

#[derive(Debug, Clone, Copy)]
pub struct PartialMatchOp {
    pub name: &'static str,
    pub kind: MatchKind,
    pub negated: bool,
    pub insensitive: bool,
    pub prefix: &'static str,
    pub suffix: &'static str,
    pub escape: bool,
    pub min_length: usize,
}

const fn op(name: &'static str, kind: MatchKind, negated: bool, insensitive: bool,
            prefix: &'static str, suffix: &'static str, escape: bool, min_length: usize) -> PartialMatchOp {
    PartialMatchOp { name, kind, negated, insensitive, prefix, suffix, escape, min_length }
}

pub const PARTIAL_MATCH_OPERATORS: &[PartialMatchOp] = &[
    op("like", MatchKind::Like, false, false, "", "", false, 0),
    op("not_like", MatchKind::Like, true, false, "", "", false, 0),
    op("ilike", MatchKind::Like, false, true, "", "", false, 0),
    op("not_ilike", MatchKind::Like, true, true, "", "", false, 0),
    op("startswith", MatchKind::Like, false, false, "", "%", true, 0),
    op("not_startswith", MatchKind::Like, true, false, "", "%", true, 0),
    op("istartswith", MatchKind::Like, false, true, "", "%", true, 0),
    op("not_istartswith", MatchKind::Like, true, true, "", "%", true, 0),
    op("endswith", MatchKind::Like, false, false, "%", "", true, 0),
    op("not_endswith", MatchKind::Like, true, false, "%", "", true, 0),
    op("iendswith", MatchKind::Like, false, true, "%", "", true, 0),
    op("not_iendswith", MatchKind::Like, true, true, "%", "", true, 0),
    op("contains", MatchKind::Like, false, false, "%", "%", true, CONTAINS_MIN_LENGTH),
    op("not_contains", MatchKind::Like, true, false, "%", "%", true, CONTAINS_MIN_LENGTH),
    op("icontains", MatchKind::Like, false, true, "%", "%", true, CONTAINS_MIN_LENGTH),
    op("not_icontains", MatchKind::Like, true, true, "%", "%", true, CONTAINS_MIN_LENGTH),
    op("regex", MatchKind::Regex, false, false, "", "", false, 0),
    op("not_regex", MatchKind::Regex, true, false, "", "", false, 0),
    op("iregex", MatchKind::Regex, false, true, "", "", false, 0),
    op("not_iregex", MatchKind::Regex, true, true, "", "", false, 0),
];

pub fn lookup(op: &str) -> Option<&'static PartialMatchOp> {
    PARTIAL_MATCH_OPERATORS.iter().find(|o| o.name == op)
}

pub fn like_escape(value: &str) -> String {
    value.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_")
}

pub fn like_escape_bang(value: &str) -> String {
    value.replace('!', "!!").replace('%', "!%").replace('_', "!_").replace('[', "![")
}

pub fn build_like_pattern(op: &PartialMatchOp, value: &str, escaper: fn(&str) -> String) -> String {
    let body = if op.escape { escaper(value) } else { value.to_string() };
    format!("{}{}{}", op.prefix, body, op.suffix)
}

pub fn sql_like_literal(pattern: &str) -> String {
    format!("'{}'", pattern.replace('\\', "\\\\").replace('\'', "''"))
}

pub fn mssql_like_literal(pattern: &str) -> String {
    format!("'{}'", pattern.replace('\'', "''"))
}

pub fn bq_like_literal(pattern: &str) -> String {
    format!("\"{}\"", pattern.replace('\\', "\\\\").replace('"', "\\\""))
}

/// Twin of `validate_partial_match` for a string operand (non-string handled by `check_entries`).
pub fn validate(key: &str, op: &PartialMatchOp, value: &str, supports_regex: bool) -> PyResult<()> {
    let name = op.name;
    let n = value.chars().count();
    if n < op.min_length {
        return Err(PyValueError::new_err(format!(
            "{name} on '{key}' requires at least 3 characters (got {n})"
        )));
    }
    if op.kind == MatchKind::Regex {
        if !supports_regex {
            return Err(PyValueError::new_err(format!(
                "{name} on '{key}': regex operators are not supported by this query parser"
            )));
        }
        if value.is_empty() {
            return Err(PyValueError::new_err(format!("{name} on '{key}': empty regex pattern")));
        }
        if n > MAX_REGEX_PATTERN_LENGTH {
            return Err(PyValueError::new_err(format!(
                "{name} on '{key}': regex pattern too long ({n} > 200 chars)"
            )));
        }
        if NESTED_QUANTIFIER_RE.is_match(value) {
            return Err(PyValueError::new_err(format!(
                "{name} on '{key}': regex pattern has a nested quantifier"
            )));
        }
    }
    Ok(())
}

/// Twin of `validate_partial_match_dict`. `entries` = (key, Some(str) | None for non-string).
/// Ok(None): no table operator present. Ok(Some((op, operand))): single valid pair.
pub fn check_entries<'a>(
    key: &str,
    entries: &[(&'a str, Option<&'a str>)],
    supports_regex: bool,
) -> PyResult<Option<(&'static PartialMatchOp, &'a str)>> {
    let Some(&(op_name, operand)) = entries.iter().find(|(k, _)| lookup(k).is_some()) else {
        return Ok(None);
    };
    if entries.len() != 1 {
        return Err(PyValueError::new_err(format!(
            "one operator per field: '{key}' combines a partial-matching operator with other keys"
        )));
    }
    let entry = lookup(op_name).expect("operator checked above");
    let Some(value) = operand else {
        return Err(PyValueError::new_err(format!(
            "{op_name} on '{key}' requires a string operand"
        )));
    };
    validate(key, entry, value, supports_regex)?;
    Ok(Some((entry, value)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn err_msg<T>(r: PyResult<T>) -> String {
        Python::initialize();
        Python::attach(|_py| match r {
            Ok(_) => panic!("expected an error"),
            Err(e) => e.to_string(),
        })
    }

    fn msg_of(r: PyResult<Option<(&'static PartialMatchOp, &str)>>) -> String {
        err_msg(r)
    }

    #[test]
    fn table_has_twenty_rows() {
        assert_eq!(PARTIAL_MATCH_OPERATORS.len(), 20);
    }

    #[test]
    fn lookup_known_and_unknown() {
        assert_eq!(lookup("icontains").unwrap().min_length, CONTAINS_MIN_LENGTH);
        assert!(lookup("nope").is_none());
    }

    #[test]
    fn escape_helpers() {
        assert_eq!(like_escape("a\\b%c_d"), "a\\\\b\\%c\\_d");
        assert_eq!(like_escape_bang("a!b%c_d[e"), "a!!b!%c!_d![e");
    }

    #[test]
    fn like_pattern_building() {
        let sw = lookup("startswith").unwrap();
        assert_eq!(build_like_pattern(sw, "50%", like_escape), "50\\%%");
        let ct = lookup("contains").unwrap();
        assert_eq!(build_like_pattern(ct, "a_b", like_escape_bang), "%a!_b%");
        let lk = lookup("like").unwrap();
        assert_eq!(build_like_pattern(lk, "a_%", like_escape), "a_%");
    }

    #[test]
    fn literals() {
        assert_eq!(sql_like_literal("a\\b'c"), "'a\\\\b''c'");
        assert_eq!(mssql_like_literal("a\\b'c"), "'a\\b''c'");
        assert_eq!(bq_like_literal("a\\b\"c"), "\"a\\\\b\\\"c\"");
    }

    #[test]
    fn validate_ok() {
        assert!(Python::attach(|_| validate("k", lookup("contains").unwrap(), "abc", false)).is_ok());
        assert!(Python::attach(|_| validate("k", lookup("regex").unwrap(), "^a+$", true)).is_ok());
    }

    #[test]
    fn validate_errors() {
        let c = lookup("contains").unwrap();
        assert_eq!(err_msg(validate("k", c, "ab", false)), "ValueError: contains on 'k' requires at least 3 characters (got 2)");
        let r = lookup("regex").unwrap();
        assert_eq!(err_msg(validate("k", r, "a", false)), "ValueError: regex on 'k': regex operators are not supported by this query parser");
        assert_eq!(err_msg(validate("k", r, "", true)), "ValueError: regex on 'k': empty regex pattern");
        let long = "a".repeat(201);
        assert_eq!(err_msg(validate("k", r, &long, true)), "ValueError: regex on 'k': regex pattern too long (201 > 200 chars)");
        assert_eq!(err_msg(validate("k", r, "(a+)+", true)), "ValueError: regex on 'k': regex pattern has a nested quantifier");
    }

    #[test]
    fn check_entries_cases() {
        Python::initialize();
        assert!(check_entries("k", &[("gt", Some("1"))], false).unwrap().is_none());
        let (e, v) = check_entries("k", &[("like", Some("a%"))], false).unwrap().unwrap();
        assert_eq!((e.name, v), ("like", "a%"));
        assert_eq!(
            msg_of(check_entries("k", &[("like", Some("a")), ("gt", Some("1"))], false)),
            "ValueError: one operator per field: 'k' combines a partial-matching operator with other keys"
        );
        assert_eq!(
            msg_of(check_entries("k", &[("like", None)], false)),
            "ValueError: like on 'k' requires a string operand"
        );
        assert_eq!(
            msg_of(check_entries("k", &[("contains", Some("ab"))], false)),
            "ValueError: contains on 'k' requires at least 3 characters (got 2)"
        );
    }
}
