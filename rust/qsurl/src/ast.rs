//! Árbol sintáctico del dialecto URL de Querysource.
//!
//! Todo lo que hay aquí es *neutral al motor*: no aparece ni un fragmento de
//! SQL, CQL, Flux ni SOQL. El AST describe la intención de la consulta
//! (qué columnas, qué condiciones, qué orden, cuántas filas) y cada driver
//! decide cómo materializarla, o si delega parte al post-filtrado en
//! dataframe cuando el backend no soporta la operación.

use serde::Serialize;

/// Literal tipado. El tipo lo fija el parser, no un sniffing posterior
/// sobre strings, para que `'2024-01-01'` (texto) y `2024-01-01` (fecha)
/// lleguen distintos al driver.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(untagged)]
pub enum Literal {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    /// ISO-8601 `YYYY-MM-DD` sin comillas.
    Date(String),
    /// ISO-8601 `YYYY-MM-DDTHH:MM[:SS][Z|±HH:MM]` sin comillas.
    DateTime(String),
}

impl Literal {
    pub fn dtype(&self) -> &'static str {
        match self {
            Literal::Null => "null",
            Literal::Bool(_) => "bool",
            Literal::Int(_) => "int",
            Literal::Float(_) => "float",
            Literal::Str(_) => "str",
            Literal::Date(_) => "date",
            Literal::DateTime(_) => "datetime",
        }
    }
}

/// Ruta a una columna. Un solo segmento es una columna del recurso;
/// varios segmentos (`store.region.name`) son *navegación* por relaciones,
/// que en la fase 1 el parser acepta pero sólo los drivers con
/// introspección de esquema saben resolver.
pub type Path = Vec<String>;

/// Operando de una comparación o argumento de función.
#[derive(Debug, Clone, PartialEq)]
pub enum Operand {
    Path(Path),
    Literal(Literal),
    /// Lista de literales: `state=('CA','NV')` → pertenencia.
    List(Vec<Literal>),
    /// Función escalar de una lista blanca: `lower(name)`, `year(opened)`.
    Call {
        name: String,
        args: Vec<Operand>,
    },
}

/// Operadores de comparación del dialecto. El nombre del variante ya es el
/// vocabulario de `expression` que Querysource usa hoy en sus filtros.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum CmpOp {
    #[serde(rename = "==")]
    Eq,
    #[serde(rename = "!=")]
    Ne,
    #[serde(rename = "<")]
    Lt,
    #[serde(rename = "<=")]
    Le,
    #[serde(rename = ">")]
    Gt,
    #[serde(rename = ">=")]
    Ge,
    #[serde(rename = "contains")]
    Contains,
    #[serde(rename = "not_contains")]
    NotContains,
    #[serde(rename = "startswith")]
    StartsWith,
    #[serde(rename = "endswith")]
    EndsWith,
    #[serde(rename = "regex")]
    Regex,
}

impl CmpOp {
    /// Operador equivalente con los operandos intercambiados
    /// (`'CA'=state` ≡ `state='CA'`, `3<x` ≡ `x>3`). `None` si no es
    /// simétrico (los operadores de texto tienen lado fijo).
    pub fn flipped(self) -> Option<CmpOp> {
        Some(match self {
            CmpOp::Eq => CmpOp::Eq,
            CmpOp::Ne => CmpOp::Ne,
            CmpOp::Lt => CmpOp::Gt,
            CmpOp::Le => CmpOp::Ge,
            CmpOp::Gt => CmpOp::Lt,
            CmpOp::Ge => CmpOp::Le,
            _ => return None,
        })
    }

    pub fn as_str(self) -> &'static str {
        match self {
            CmpOp::Eq => "==",
            CmpOp::Ne => "!=",
            CmpOp::Lt => "<",
            CmpOp::Le => "<=",
            CmpOp::Gt => ">",
            CmpOp::Ge => ">=",
            CmpOp::Contains => "contains",
            CmpOp::NotContains => "not_contains",
            CmpOp::StartsWith => "startswith",
            CmpOp::EndsWith => "endswith",
            CmpOp::Regex => "regex",
        }
    }
}

/// Expresión booleana del filtro (`?...`).
#[derive(Debug, Clone, PartialEq)]
pub enum Expr {
    Compare {
        lhs: Operand,
        op: CmpOp,
        rhs: Operand,
    },
    /// Operando "desnudo" en posición booleana: `?active` o `?email`
    /// (≈ *no nulo / verdadero*, como en HTSQL).
    Truthy(Operand),
    Not(Box<Expr>),
    And(Vec<Expr>),
    Or(Vec<Expr>),
}

impl Expr {
    /// Construye un AND aplanado (evita árboles binarios profundos).
    pub fn and(l: Expr, r: Expr) -> Expr {
        match l {
            Expr::And(mut v) => {
                v.push(r);
                Expr::And(v)
            }
            other => Expr::And(vec![other, r]),
        }
    }

    pub fn or(l: Expr, r: Expr) -> Expr {
        match l {
            Expr::Or(mut v) => {
                v.push(r);
                Expr::Or(v)
            }
            other => Expr::Or(vec![other, r]),
        }
    }
}

/// Columna seleccionada en `{...}`, con alias opcional (`city:as(town)`).
#[derive(Debug, Clone, PartialEq)]
pub struct Field {
    pub path: Path,
    pub alias: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SortKey {
    pub path: Path,
    pub descending: bool,
}

/// Consulta completa: `slug{fields}?filter:op(...):op(...)`.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Query {
    pub slug: String,
    pub fields: Vec<Field>,
    pub filter: Option<Expr>,
    pub sort: Vec<SortKey>,
    pub limit: Option<u64>,
    pub offset: Option<u64>,
    pub distinct: bool,
}

/// Operador de la tubería (`:sort(...)`, `:top(n)`, ...). Es un enum cerrado a
/// propósito: cualquier cosa fuera de aquí es un error de parseo con la lista
/// de operadores válidos, que es exactamente el feedback que un LLM necesita
/// para autocorregirse.
#[derive(Debug, Clone, PartialEq)]
pub enum PipeOp {
    Sort(Vec<SortKey>),
    Top(u64),
    Skip(u64),
    Distinct,
}

impl PipeOp {
    pub const KNOWN: &'static [&'static str] =
        &["sort", "top", "limit", "skip", "offset", "distinct"];
}
