//! Lowering del AST al IR JSON que consume Querysource.
//!
//! La forma de las hojas del filtro es deliberadamente la que Querysource ya
//! usa (`{"column": ..., "expression": ..., "value": ...}`), de modo que el
//! ejecutor actual, los slugs y los formatos de salida no cambian. Lo único
//! nuevo es que el filtro puede ser un árbol (`and`/`or`/`not`) en vez de una
//! lista implícitamente AND.
//!
//! Además de la consulta, el IR lleva `requires`: el conjunto de capacidades
//! que la consulta necesita del backend. Cada driver declara las suyas y la
//! diferencia se resuelve por *pushdown parcial*: lo que el motor no sabe
//! hacer (un `regex` en Cassandra, un `or` en la API de Salesforce Reports,
//! un `offset` en InfluxDB) se ejecuta en la etapa de post-filtrado sobre el
//! dataframe, que es universal.

use std::collections::BTreeSet;

use serde::Serialize;
use serde_json::{Map, Value, json};

use crate::ast::*;

/// Capacidades que una consulta puede exigir del backend.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Feature {
    Select,
    Alias,
    Filter,
    Or,
    Not,
    InList,
    NullCheck,
    TextMatch,
    Regex,
    Functions,
    Navigation,
    Sort,
    Limit,
    Offset,
    Distinct,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LowerError(pub String);

impl std::fmt::Display for LowerError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for LowerError {}

struct Ctx {
    requires: BTreeSet<Feature>,
}

impl Ctx {
    fn need(&mut self, f: Feature) {
        self.requires.insert(f);
    }
}

fn path_str(p: &Path) -> String {
    p.join(".")
}

fn lower_operand(op: &Operand, cx: &mut Ctx) -> Value {
    match op {
        Operand::Path(p) => {
            if p.len() > 1 {
                cx.need(Feature::Navigation);
            }
            Value::String(path_str(p))
        }
        Operand::Literal(l) => json!(l),
        Operand::List(items) => {
            cx.need(Feature::InList);
            json!(items)
        }
        Operand::Call { name, args } => {
            cx.need(Feature::Functions);
            json!({
                "fn": name,
                "args": args.iter().map(|a| lower_operand(a, cx)).collect::<Vec<_>>(),
            })
        }
    }
}

fn is_column(op: &Operand) -> bool {
    matches!(op, Operand::Path(_) | Operand::Call { .. })
}

/// Hoja `{column, expression, value}` con las normalizaciones que Querysource
/// espera: `=null` → `is_null`, `!=null` → `not_null`, lista → `isin`.
fn leaf(column: &Operand, op: CmpOp, value: &Operand, cx: &mut Ctx) -> Result<Value, LowerError> {
    let mut m = Map::new();
    m.insert("column".into(), lower_operand(column, cx));

    match (op, value) {
        (CmpOp::Eq, Operand::Literal(Literal::Null)) => {
            cx.need(Feature::NullCheck);
            m.insert("expression".into(), json!("is_null"));
        }
        (CmpOp::Ne, Operand::Literal(Literal::Null)) => {
            cx.need(Feature::NullCheck);
            m.insert("expression".into(), json!("not_null"));
        }
        (CmpOp::Eq | CmpOp::Ne, Operand::List(_)) => {
            m.insert("expression".into(), json!(op.as_str()));
            m.insert("value".into(), lower_operand(value, cx));
        }
        (_, Operand::List(_)) => {
            return Err(LowerError(format!(
                "operator `{}` does not accept a list; use `=` or `!=` for membership",
                op.as_str()
            )));
        }
        (CmpOp::Regex, v) => {
            cx.need(Feature::Regex);
            m.insert("expression".into(), json!(op.as_str()));
            m.insert("value".into(), lower_operand(v, cx));
        }
        (CmpOp::Contains | CmpOp::NotContains | CmpOp::StartsWith | CmpOp::EndsWith, v) => {
            cx.need(Feature::TextMatch);
            m.insert("expression".into(), json!(op.as_str()));
            m.insert("value".into(), lower_operand(v, cx));
        }
        (_, v) => {
            m.insert("expression".into(), json!(op.as_str()));
            m.insert("value".into(), lower_operand(v, cx));
        }
    }

    // Tipo del literal, para que el driver no tenga que adivinar si
    // `2024-01-01` es texto o fecha.
    if let Operand::Literal(l @ (Literal::Date(_) | Literal::DateTime(_))) = value {
        m.insert("dtype".into(), json!(l.dtype()));
    }
    Ok(Value::Object(m))
}

fn lower_expr(e: &Expr, cx: &mut Ctx) -> Result<Value, LowerError> {
    match e {
        Expr::Compare { lhs, op, rhs } => {
            if is_column(lhs) {
                leaf(lhs, *op, rhs, cx)
            } else if is_column(rhs) {
                // `'CA'=state` → `state='CA'`; `3<price` → `price>3`.
                match op.flipped() {
                    Some(f) => leaf(rhs, f, lhs, cx),
                    None => Err(LowerError(format!(
                        "operator `{}` requires the column on the left-hand side",
                        op.as_str()
                    ))),
                }
            } else {
                Err(LowerError(
                    "a comparison needs a column or function on at least one side".into(),
                ))
            }
        }
        Expr::Truthy(op) => {
            if !is_column(op) {
                return Err(LowerError(
                    "a bare literal is not a condition; compare it against a column".into(),
                ));
            }
            cx.need(Feature::NullCheck);
            Ok(json!({ "column": lower_operand(op, cx), "expression": "not_null" }))
        }
        Expr::Not(inner) => {
            // `!email` es azúcar para `email=null`; el resto es un NOT real.
            if let Expr::Truthy(op) = inner.as_ref()
                && is_column(op)
            {
                cx.need(Feature::NullCheck);
                return Ok(json!({ "column": lower_operand(op, cx), "expression": "is_null" }));
            }
            cx.need(Feature::Not);
            Ok(json!({ "not": lower_expr(inner, cx)? }))
        }
        Expr::And(items) => {
            let v = items
                .iter()
                .map(|i| lower_expr(i, cx))
                .collect::<Result<Vec<_>, _>>()?;
            Ok(json!({ "and": v }))
        }
        Expr::Or(items) => {
            cx.need(Feature::Or);
            let v = items
                .iter()
                .map(|i| lower_expr(i, cx))
                .collect::<Result<Vec<_>, _>>()?;
            Ok(json!({ "or": v }))
        }
    }
}

/// Convierte una `Query` en el IR JSON de Querysource.
pub fn lower(q: &Query) -> Result<Value, LowerError> {
    let mut cx = Ctx {
        requires: BTreeSet::new(),
    };

    let fields: Vec<Value> = q
        .fields
        .iter()
        .map(|f| {
            if f.path.len() > 1 {
                cx.need(Feature::Navigation);
            }
            match &f.alias {
                None => Value::String(path_str(&f.path)),
                Some(a) => {
                    cx.need(Feature::Alias);
                    json!({ "column": path_str(&f.path), "alias": a })
                }
            }
        })
        .collect();
    if !fields.is_empty() {
        cx.need(Feature::Select);
    }

    let filter = match &q.filter {
        Some(e) => {
            cx.need(Feature::Filter);
            // Un filtro de una sola hoja se entrega como `{"and":[hoja]}` para
            // que el consumidor tenga siempre la misma forma en la raíz.
            let v = lower_expr(e, &mut cx)?;
            match v {
                Value::Object(ref m) if m.contains_key("and") || m.contains_key("or") => v,
                other => json!({ "and": [other] }),
            }
        }
        None => Value::Null,
    };

    let sort: Vec<Value> = q
        .sort
        .iter()
        .map(|k| {
            if k.path.len() > 1 {
                cx.need(Feature::Navigation);
            }
            json!({ "column": path_str(&k.path), "order": if k.descending { "desc" } else { "asc" } })
        })
        .collect();
    if !sort.is_empty() {
        cx.need(Feature::Sort);
    }
    if q.limit.is_some() {
        cx.need(Feature::Limit);
    }
    if q.offset.is_some() {
        cx.need(Feature::Offset);
    }
    if q.distinct {
        cx.need(Feature::Distinct);
    }

    Ok(json!({
        "slug": q.slug,
        "fields": fields,
        "filter": filter,
        "sort": sort,
        "limit": q.limit,
        "offset": q.offset,
        "distinct": q.distinct,
        "requires": cx.requires,
    }))
}
