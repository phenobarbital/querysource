//! `qsurl`: parser del dialecto URL (estilo HTSQL) de Querysource.
//!
//! Convierte una ruta como
//!
//! ```text
//! /queries/hisense_stores{store_id,name,city}?state_code='CA'&opened>=2024-01-01:sort(name):top(50)
//! ```
//!
//! en el IR JSON neutral al motor que Querysource ya ejecuta contra
//! Postgres, Cassandra, BigQuery, InfluxDB, Salesforce o una API REST.
//! El parser no sabe nada de los motores: produce intención, y los drivers
//! deciden qué empujan al backend y qué post-filtran.

pub mod ast;
pub mod ir;
pub mod parser;

#[cfg(feature = "python")]
mod python;

use chumsky::Parser;
use serde::Serialize;

pub use ast::Query;
pub use ir::{Feature, LowerError};

/// Error de parseo pensado para ser leído por un LLM además de por un humano:
/// posición, qué se encontró, qué se esperaba y un puntero visual.
#[derive(Debug, Clone, Serialize)]
pub struct ParseError {
    pub offset: usize,
    pub message: String,
    pub found: Option<String>,
    pub expected: Vec<String>,
    /// La consulta con un `^` bajo la posición del error.
    pub pointer: String,
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}\n{}", self.message, self.pointer)
    }
}

impl std::error::Error for ParseError {}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Error {
    Parse(ParseError),
    Lower { message: String },
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Error::Parse(e) => write!(f, "{e}"),
            Error::Lower { message } => write!(f, "{message}"),
        }
    }
}

impl std::error::Error for Error {}

impl Error {
    pub fn to_json(&self) -> String {
        serde_json::to_string(self).expect("error is serializable")
    }
}

fn pointer(src: &str, offset: usize) -> String {
    let col = src[..offset.min(src.len())].chars().count();
    format!("{src}\n{}^", " ".repeat(col))
}

/// Parsea la URL (ya percent-decoded) a un `Query`.
pub fn parse(src: &str) -> Result<Query, ParseError> {
    let src = src.trim();
    parser::query().parse(src).into_result().map_err(|errs| {
        // El error más avanzado en la entrada suele ser el más informativo.
        let e = errs
            .into_iter()
            .max_by_key(|e| e.span().start)
            .expect("at least one error");
        let offset = e.span().start;
        let expected: Vec<String> = e.expected().map(|p| p.to_string()).collect();
        let found = e.found().map(|c| c.to_string());
        let message = match e.reason() {
            chumsky::error::RichReason::Custom(msg) => msg.clone(),
            _ => {
                let what = match &found {
                    Some(c) => format!("unexpected `{c}`"),
                    None => "unexpected end of query".to_string(),
                };
                if expected.is_empty() {
                    format!("{what} at position {offset}")
                } else {
                    format!(
                        "{what} at position {offset}; expected {}",
                        expected.join(", ")
                    )
                }
            }
        };
        ParseError {
            offset,
            message,
            found,
            expected,
            pointer: pointer(src, offset),
        }
    })
}

/// Parsea y baja al IR JSON de Querysource en un solo paso.
pub fn parse_to_ir(src: &str) -> Result<serde_json::Value, Error> {
    let q = parse(src).map_err(Error::Parse)?;
    ir::lower(&q).map_err(|e| Error::Lower { message: e.0 })
}

/// Igual que [`parse_to_ir`] pero devuelve el JSON serializado
/// (lo que expone el binding Python).
pub fn parse_to_json(src: &str) -> Result<String, Error> {
    parse_to_ir(src).map(|v| serde_json::to_string(&v).expect("ir is serializable"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const EXAMPLE: &str = "/queries/hisense_stores{store_id,name,city}?state_code='CA'&opened>=2024-01-01:sort(name):top(50)";

    #[test]
    fn example_query_lowers_to_querysource_ir() {
        let ir = parse_to_ir(EXAMPLE).unwrap();
        assert_eq!(ir["slug"], "hisense_stores");
        assert_eq!(ir["fields"], json!(["store_id", "name", "city"]));
        assert_eq!(
            ir["filter"],
            json!({"and": [
                {"column": "state_code", "expression": "==", "value": "CA"},
                {"column": "opened", "expression": ">=", "value": "2024-01-01", "dtype": "date"},
            ]})
        );
        assert_eq!(ir["sort"], json!([{"column": "name", "order": "asc"}]));
        assert_eq!(ir["limit"], 50);
        assert_eq!(ir["offset"], json!(null));
        assert_eq!(ir["requires"], json!(["select", "filter", "sort", "limit"]));
    }

    #[test]
    fn works_without_prefix_selection_or_filter() {
        let ir = parse_to_ir("hisense_stores").unwrap();
        assert_eq!(ir["slug"], "hisense_stores");
        assert_eq!(ir["fields"], json!([]));
        assert_eq!(ir["filter"], json!(null));
        assert_eq!(ir["requires"], json!([]));
    }

    #[test]
    fn tolerates_decoded_whitespace() {
        let a =
            parse_to_ir("stores ? state = 'CA' & city ~ 'san' : sort( -opened , name ) : top( 5 )")
                .unwrap();
        let b = parse_to_ir("stores?state='CA'&city~'san':sort(-opened,name):top(5)").unwrap();
        assert_eq!(a, b);
        assert_eq!(
            a["sort"],
            json!([{"column": "opened", "order": "desc"}, {"column": "name", "order": "asc"}])
        );
    }

    #[test]
    fn precedence_or_and_not_and_parens() {
        let ir = parse_to_ir("s?a=1|b=2&!c=3").unwrap();
        assert_eq!(
            ir["filter"],
            json!({"or": [
                {"column": "a", "expression": "==", "value": 1},
                {"and": [
                    {"column": "b", "expression": "==", "value": 2},
                    {"not": {"column": "c", "expression": "==", "value": 3}},
                ]},
            ]})
        );
        let ir = parse_to_ir("s?(a=1|b=2)&c=3").unwrap();
        assert_eq!(ir["filter"]["and"][0]["or"].as_array().unwrap().len(), 2);
    }

    #[test]
    fn null_checks_lists_and_text_operators() {
        let ir = parse_to_ir("s?email!=null&phone=null&!fax&active&state=('CA','NV')&name^='San'&city$='go'&zip=~'^9'&notes!~'closed'").unwrap();
        let leaves = ir["filter"]["and"].as_array().unwrap();
        assert_eq!(
            leaves[0],
            json!({"column": "email", "expression": "not_null"})
        );
        assert_eq!(
            leaves[1],
            json!({"column": "phone", "expression": "is_null"})
        );
        assert_eq!(leaves[2], json!({"column": "fax", "expression": "is_null"}));
        assert_eq!(
            leaves[3],
            json!({"column": "active", "expression": "not_null"})
        );
        assert_eq!(
            leaves[4],
            json!({"column": "state", "expression": "==", "value": ["CA", "NV"]})
        );
        assert_eq!(leaves[5]["expression"], "startswith");
        assert_eq!(leaves[6]["expression"], "endswith");
        assert_eq!(leaves[7]["expression"], "regex");
        assert_eq!(leaves[8]["expression"], "not_contains");
        assert_eq!(
            ir["requires"],
            json!(["filter", "in_list", "null_check", "text_match", "regex"])
        );
    }

    #[test]
    fn functions_navigation_alias_and_flipped_comparison() {
        let ir = parse_to_ir(
            "s{id,store.region.name:as(region)}?lower(name)='acme'&100<price&year(opened)=2024",
        )
        .unwrap();
        assert_eq!(
            ir["fields"][1],
            json!({"column": "store.region.name", "alias": "region"})
        );
        let leaves = ir["filter"]["and"].as_array().unwrap();
        assert_eq!(
            leaves[0],
            json!({"column": {"fn": "lower", "args": ["name"]}, "expression": "==", "value": "acme"})
        );
        assert_eq!(
            leaves[1],
            json!({"column": "price", "expression": ">", "value": 100})
        );
        assert_eq!(leaves[2]["column"]["fn"], "year");
        assert_eq!(
            ir["requires"],
            json!(["select", "alias", "filter", "functions", "navigation"])
        );
    }

    #[test]
    fn literals_are_typed() {
        let ir = parse_to_ir(
            "s?a=1&b=-2.5&c=true&d='it''s'&e=\"q\\\"q\"&f=2024-01-01T10:30:00Z&g='2024-01-01'",
        )
        .unwrap();
        let l = ir["filter"]["and"].as_array().unwrap();
        assert_eq!(l[0]["value"], 1);
        assert_eq!(l[1]["value"], -2.5);
        assert_eq!(l[2]["value"], true);
        assert_eq!(l[3]["value"], "it's");
        assert_eq!(l[4]["value"], "q\"q");
        assert_eq!(l[5]["dtype"], "datetime");
        assert_eq!(l[6]["value"], "2024-01-01");
        assert!(l[6].get("dtype").is_none(), "quoted date stays a string");
    }

    #[test]
    fn unknown_pipe_operator_lists_valid_ones() {
        let err = parse("s:order(name)").unwrap_err();
        assert!(
            err.message.contains("unknown pipeline operator `:order`"),
            "{}",
            err.message
        );
        assert!(err.message.contains(":sort"));
    }

    #[test]
    fn syntax_error_points_at_offset() {
        let err = parse("s?state=='CA'&&city='x'").unwrap_err();
        assert_eq!(err.offset, 14, "{err}");
        assert!(err.pointer.lines().nth(1).unwrap().ends_with('^'));
    }

    #[test]
    fn lowering_errors_are_explicit() {
        let e = parse_to_ir("s?name~('a','b')").unwrap_err();
        assert!(e.to_string().contains("does not accept a list"));
        let e = parse_to_ir("s?'x'~name").unwrap_err();
        assert!(e.to_string().contains("left-hand side"));
        let e = parse_to_ir("s?1=2").unwrap_err();
        assert!(e.to_string().contains("needs a column"));
    }

    #[test]
    fn duplicate_top_is_rejected() {
        assert!(parse("s:top(1):top(2)").is_err());
    }

    #[test]
    fn requires_is_declaration_ordered() {
        let ir = parse_to_ir("s{a,b:as(c)}?x=1&!y:sort(a):top(1)").unwrap();
        // Bounded by the `Feature` enum declaration order (ir.rs), NOT alphabetical.
        assert_eq!(
            ir["requires"],
            json!(["select", "alias", "filter", "null_check", "sort", "limit"])
        );
    }

    #[test]
    fn lower_error_json_has_only_kind_and_message() {
        let err = parse_to_json("s?a~('x')").unwrap_err();
        assert_eq!(
            err.to_json(),
            r#"{"kind":"lower","message":"operator `contains` does not accept a list; use `=` or `!=` for membership"}"#
        );
    }
}
