// Copyright (C) 2018-present Jesus Lara
//
// pgsql_unnest.rs — JSONB array unnest planner (FEAT-153). Rust fast path of
// querysource/parsers/jsonb_unnest.pyx; output and error messages must be identical.

#![allow(dead_code)]

use once_cell::sync::Lazy;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyBool, PyDict, PyFloat, PyList, PyString};
use regex::Regex;

pub(crate) const ARRAY_ALIAS: &str = "_qs_e0";
pub(crate) const SOURCE_ALIAS: &str = "_qs_src";
pub(crate) const ALLOWED_CASTS: &[&str] = &[
    "text",
    "int",
    "integer",
    "bigint",
    "numeric",
    "float",
    "date",
    "timestamp",
    "timestamptz",
    "boolean",
];
pub(crate) const AGGREGATES: &[&str] = &["count", "min", "max", "sum", "avg"];
pub(crate) const BUCKETS: &[&str] = &["year", "quarter", "month", "week", "day"];
pub(crate) const CONFIG_KEYS: &[&str] = &["columns", "aliases", "strict", "safe_cast"];
pub(crate) const COLUMN_KEYS: &[&str] = &["empty", "safe_cast", "prefilter"];
pub(crate) const HAVING_OPERATORS: &[&str] = &["=", ">=", "<=", "<>", "!=", "<", ">"];

/// Python value tree converted once at the boundary (insertion order preserved).
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum UValue {
    None,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    List(Vec<UValue>),
    Dict(Vec<(String, UValue)>),
    Other,
}

/// Convert a Python object into a `UValue` (bool before int — Python bool subclasses int).
pub(crate) fn from_py(obj: &Bound<'_, PyAny>) -> UValue {
    if obj.is_none() {
        return UValue::None;
    }
    if let Ok(b) = obj.cast::<PyBool>() {
        return UValue::Bool(b.is_true());
    }
    if obj.cast::<PyFloat>().is_ok() {
        return match obj.extract::<f64>() {
            Ok(f) => UValue::Float(f),
            Err(_) => UValue::Other,
        };
    }
    if obj.cast::<PyString>().is_ok() {
        return match obj.extract::<String>() {
            Ok(s) => UValue::Str(s),
            Err(_) => UValue::Other,
        };
    }
    if let Ok(dict) = obj.cast::<PyDict>() {
        let mut entries: Vec<(String, UValue)> = Vec::with_capacity(dict.len());
        for (key, value) in dict.iter() {
            match key.extract::<String>() {
                Ok(k) => entries.push((k, from_py(&value))),
                Err(_) => return UValue::Other,
            }
        }
        return UValue::Dict(entries);
    }
    if let Ok(list) = obj.cast::<PyList>() {
        return UValue::List(list.iter().map(|item| from_py(&item)).collect());
    }
    if let Ok(i) = obj.extract::<i64>() {
        return UValue::Int(i);
    }
    UValue::Other
}

impl UValue {
    /// Python `bool(value)` truthiness.
    pub(crate) fn truthy(&self) -> bool {
        match self {
            UValue::None => false,
            UValue::Bool(b) => *b,
            UValue::Int(i) => *i != 0,
            UValue::Float(f) => *f != 0.0,
            UValue::Str(s) => !s.is_empty(),
            UValue::List(l) => !l.is_empty(),
            UValue::Dict(d) => !d.is_empty(),
            UValue::Other => true,
        }
    }

    fn as_dict(&self) -> Option<&Vec<(String, UValue)>> {
        match self {
            UValue::Dict(d) => Some(d),
            _ => None,
        }
    }
}

/// PostgreSQL literal — identical to pgsql_parser.rs:51 / jsonb_unnest.pyx `_pg_literal`.
pub(crate) fn pg_literal(value: &str) -> String {
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

// ---------------------------------------------------------------------------
// Grammar and config
// ---------------------------------------------------------------------------

static IDENT_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$").unwrap());
static REF_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^(?P<col>[A-Za-z_][A-Za-z0-9_]{0,62})(?:\[\]\.(?P<keys>[A-Za-z0-9_-]{1,128}(?:\.[A-Za-z0-9_-]{1,128})*))?(?:\s*::\s*(?P<cast>[A-Za-z]+))?$").unwrap()
});
static FUNC_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?s)^(?P<func>[A-Za-z]+)\s*\(\s*(?P<body>.*?)\s*\)$").unwrap());
static DISTINCT_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?is)^distinct\s+(?P<rest>.+)$").unwrap());
static AS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)\s+as\s+").unwrap());
static ORDER_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?is)^(?P<body>.+?)(?:\s+(?P<dir>asc|desc))?(?:\s+nulls\s+(?P<nulls>first|last))?$")
        .unwrap()
});

pub(crate) fn is_ident(text: &str) -> bool {
    IDENT_RE.is_match(text)
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Ref {
    pub column: String,
    pub keys: Vec<String>,
    pub cast: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) enum Expr {
    Ref(Ref),
    CountStar,
    /// `arg` is `Expr::Ref` or `Expr::Bucket`.
    Agg {
        func: String,
        distinct: bool,
        arg: Box<Expr>,
    },
    Bucket {
        unit: String,
        arg: Ref,
    },
}

impl Expr {
    pub(crate) fn is_aggregate(&self) -> bool {
        matches!(self, Expr::CountStar | Expr::Agg { .. })
    }
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Item {
    pub text: String,
    pub expr: Expr,
    pub name: Option<String>,
    pub alias: Option<String>,
    pub direction: Option<String>,
    pub nulls: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct ColumnCfg {
    pub empty: String,
    pub safe_cast: Option<bool>,
    pub prefilter: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Config {
    pub columns: Option<Vec<(String, ColumnCfg)>>,
    pub aliases: Vec<(String, String)>,
    pub strict: bool,
    pub safe_cast: bool,
}

impl Config {
    fn alias(&self, name: &str) -> Option<&str> {
        self.aliases
            .iter()
            .find(|(n, _)| n == name)
            .map(|(_, v)| v.as_str())
    }

    fn column(&self, name: &str) -> Option<&ColumnCfg> {
        self.columns
            .as_ref()
            .and_then(|cols| cols.iter().find(|(n, _)| n == name).map(|(_, c)| c))
    }
}

/// Parse `col`, `col::cast` or `col[].k1.k2[::cast]`.
pub(crate) fn parse_ref(text: &str) -> Result<Ref, String> {
    let caps = match REF_RE.captures(text.trim()) {
        Some(c) => c,
        None => return Err(format!("jsonb_unnest: invalid reference '{}'", text)),
    };
    let cast = match caps.name("cast") {
        Some(m) => {
            let cast = m.as_str().to_lowercase();
            if !ALLOWED_CASTS.contains(&cast.as_str()) {
                return Err(format!("jsonb_unnest: unknown cast '{}'", cast));
            }
            Some(cast)
        }
        None => None,
    };
    let keys = match caps.name("keys") {
        Some(m) => m.as_str().split('.').map(|k| k.to_string()).collect(),
        None => Vec::new(),
    };
    Ok(Ref {
        column: caps["col"].to_string(),
        keys,
        cast,
    })
}

/// Parse an expression per the grammar (count(*), agg(ref|bucket), bucket(ref), ref).
pub(crate) fn parse_expr(text: &str) -> Result<Expr, String> {
    let stripped = text.trim();
    let invalid = || format!("jsonb_unnest: invalid expression '{}'", text);
    let caps = match FUNC_RE.captures(stripped) {
        Some(c) => c,
        None => return Ok(Expr::Ref(parse_ref(stripped)?)),
    };
    let func = caps["func"].to_lowercase();
    let body = caps.name("body").map(|m| m.as_str()).unwrap_or("");
    if body.is_empty() || body.to_lowercase() == "distinct" {
        return Err(invalid());
    }
    if func == "count" {
        if body == "*" {
            return Ok(Expr::CountStar);
        }
        let distinct_caps = DISTINCT_RE.captures(body);
        let rest = match &distinct_caps {
            Some(c) => c["rest"].trim(),
            None => body,
        };
        if rest == "*" {
            return Err(invalid());
        }
        return Ok(Expr::Agg {
            func,
            distinct: distinct_caps.is_some(),
            arg: Box::new(Expr::Ref(parse_ref(rest)?)),
        });
    }
    if AGGREGATES.contains(&func.as_str()) {
        if DISTINCT_RE.is_match(body) {
            return Err(invalid());
        }
        return match FUNC_RE.captures(body) {
            None => Ok(Expr::Agg {
                func,
                distinct: false,
                arg: Box::new(Expr::Ref(parse_ref(body)?)),
            }),
            Some(inner) => {
                let unit = inner["func"].to_lowercase();
                let inner_body = inner.name("body").map(|m| m.as_str()).unwrap_or("");
                if !BUCKETS.contains(&unit.as_str())
                    || inner_body.is_empty()
                    || inner_body.contains('(')
                {
                    return Err(invalid());
                }
                Ok(Expr::Agg {
                    func,
                    distinct: false,
                    arg: Box::new(Expr::Bucket {
                        unit,
                        arg: parse_ref(inner_body)?,
                    }),
                })
            }
        };
    }
    if BUCKETS.contains(&func.as_str()) {
        if body.contains('(') {
            return Err(invalid());
        }
        return Ok(Expr::Bucket {
            unit: func,
            arg: parse_ref(body)?,
        });
    }
    Err(invalid())
}

/// Parse `<expr> [as <ident>]` (`as` case-insensitive).
pub(crate) fn parse_select_item(text: &str) -> Result<Item, String> {
    let stripped = text.trim();
    let mut split_at: Option<(usize, usize)> = None;
    for m in AS_RE.find_iter(stripped) {
        let head = &stripped[..m.start()];
        if head.matches('(').count() == head.matches(')').count() {
            split_at = Some((m.start(), m.end()));
        }
    }
    match split_at {
        None => Ok(Item {
            text: text.to_string(),
            expr: parse_expr(stripped)?,
            name: None,
            alias: None,
            direction: None,
            nulls: None,
        }),
        Some((start, end)) => {
            let alias = stripped[end..].trim();
            if !is_ident(alias) {
                return Err(format!("jsonb_unnest: invalid select item '{}'", text));
            }
            Ok(Item {
                text: text.to_string(),
                expr: parse_expr(&stripped[..start])?,
                name: None,
                alias: Some(alias.to_string()),
                direction: None,
                nulls: None,
            })
        }
    }
}

/// Parse `(<expr> | <ident>) [asc|desc] [nulls first|last]`.
pub(crate) fn parse_order_item(text: &str) -> Result<Item, String> {
    let invalid = || format!("jsonb_unnest: invalid order item '{}'", text);
    let caps = match ORDER_RE.captures(text.trim()) {
        Some(c) => c,
        None => return Err(invalid()),
    };
    let body = caps["body"].trim();
    let direction = caps.name("dir").map(|m| m.as_str().to_uppercase());
    let nulls = caps.name("nulls").map(|m| m.as_str().to_uppercase());
    let expr = match parse_expr(body) {
        Ok(e) => e,
        Err(msg) => {
            if msg.starts_with("jsonb_unnest: unknown cast") {
                return Err(msg);
            }
            return Err(invalid());
        }
    };
    let name = if is_ident(body) {
        Some(body.to_string())
    } else {
        None
    };
    Ok(Item {
        text: text.to_string(),
        expr,
        name,
        alias: None,
        direction,
        nulls,
    })
}

/// Validate and normalise `attributes['jsonb_unnest']`.
pub(crate) fn validate_config(config: &UValue) -> Result<Config, String> {
    let mut result = Config {
        columns: None,
        aliases: Vec::new(),
        strict: false,
        safe_cast: false,
    };
    let entries = match config {
        UValue::None => return Ok(result),
        UValue::Dict(d) => d,
        _ => return Err("jsonb_unnest: invalid config: must be a mapping".to_string()),
    };
    for (key, _) in entries {
        if !CONFIG_KEYS.contains(&key.as_str()) {
            return Err(format!("jsonb_unnest: invalid config: unknown key '{}'", key));
        }
    }
    let get = |name: &str| entries.iter().find(|(k, _)| k == name).map(|(_, v)| v);
    if let Some(columns) = get("columns") {
        let columns_error =
            "jsonb_unnest: invalid config: columns must map identifiers to mappings".to_string();
        let column_entries = match columns.as_dict() {
            Some(d) => d,
            None => return Err(columns_error),
        };
        let mut normalized: Vec<(String, ColumnCfg)> = Vec::new();
        for (name, options) in column_entries {
            if !is_ident(name) {
                return Err(columns_error);
            }
            let option_entries = match options.as_dict() {
                Some(d) => d,
                None => return Err(columns_error),
            };
            let mut entry = ColumnCfg {
                empty: "exclude".to_string(),
                safe_cast: None,
                prefilter: false,
            };
            for (option, value) in option_entries {
                if !COLUMN_KEYS.contains(&option.as_str()) {
                    return Err(format!(
                        "jsonb_unnest: invalid config: unknown column option '{}'",
                        option
                    ));
                }
                match option.as_str() {
                    "empty" => match value {
                        UValue::Str(s) if s == "exclude" || s == "include" => {
                            entry.empty = s.clone();
                        }
                        _ => {
                            return Err(
                                "jsonb_unnest: invalid config: empty must be 'exclude' or 'include'"
                                    .to_string(),
                            )
                        }
                    },
                    _ => match value {
                        UValue::Bool(b) => {
                            if option == "safe_cast" {
                                entry.safe_cast = Some(*b);
                            } else {
                                entry.prefilter = *b;
                            }
                        }
                        _ => {
                            return Err(format!(
                                "jsonb_unnest: invalid config: {} must be a boolean",
                                option
                            ))
                        }
                    },
                }
            }
            normalized.push((name.clone(), entry));
        }
        result.columns = Some(normalized);
    }
    if let Some(aliases) = get("aliases") {
        let aliases_error =
            "jsonb_unnest: invalid config: aliases must map identifiers to expressions".to_string();
        let alias_entries = match aliases.as_dict() {
            Some(d) => d,
            None => return Err(aliases_error),
        };
        for (name, expression) in alias_entries {
            if !is_ident(name) {
                return Err(aliases_error);
            }
            let expression = match expression {
                UValue::Str(s) => s,
                _ => return Err(aliases_error),
            };
            parse_expr(expression)?;
            result.aliases.push((name.clone(), expression.clone()));
        }
    }
    for flag in ["strict", "safe_cast"] {
        if let Some(value) = get(flag) {
            match value {
                UValue::Bool(b) => {
                    if flag == "strict" {
                        result.strict = *b;
                    } else {
                        result.safe_cast = *b;
                    }
                }
                _ => {
                    return Err(format!(
                        "jsonb_unnest: invalid config: {} must be a boolean",
                        flag
                    ))
                }
            }
        }
    }
    Ok(result)
}

fn first_token(text: &str) -> &str {
    text.split_whitespace().next().unwrap_or("")
}

/// Cheap activation check; never panics.
pub(crate) fn is_plan_candidate(
    fields: &[String],
    grouping: &[String],
    ordering: &[String],
    filter_keys: &[String],
    having: &UValue,
    config: &UValue,
) -> bool {
    if having.truthy() {
        return true;
    }
    let mut names: Vec<&str> = Vec::new();
    if let UValue::Dict(entries) = config {
        if let Some((_, UValue::Dict(aliases))) = entries.iter().find(|(k, _)| k == "aliases") {
            names = aliases.iter().map(|(k, _)| k.as_str()).collect();
        }
    }
    for entries in [fields, grouping, ordering] {
        for entry in entries {
            if entry.contains("[].") {
                return true;
            }
            if !names.is_empty() && names.contains(&first_token(entry)) {
                return true;
            }
        }
    }
    for key in filter_keys {
        if key.contains("[].") {
            return true;
        }
        if !names.is_empty() && names.contains(&key.trim_end_matches('!')) {
            return true;
        }
    }
    false
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

pub(crate) const SAFE_CAST_PATTERNS: &[(&str, &str)] = &[
    ("date", "^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"),
    ("timestamp", "^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"),
    ("timestamptz", "^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"),
    ("int", "^-?[0-9]+$"),
    ("integer", "^-?[0-9]+$"),
    ("bigint", "^-?[0-9]+$"),
    ("numeric", "^-?[0-9]+([.][0-9]+)?$"),
    ("float", "^-?[0-9]+([.][0-9]+)?$"),
    ("boolean", "^(true|false)$"),
];

fn safe_cast_pattern(cast: &str) -> Option<&'static str> {
    SAFE_CAST_PATTERNS
        .iter()
        .find(|(name, _)| *name == cast)
        .map(|(_, pattern)| *pattern)
}

/// Render a reference per the rendering table.
pub(crate) fn render_ref(r: &Ref, safe_cast: bool, implicit_cast: Option<&str>) -> String {
    if r.keys.is_empty() {
        return match &r.cast {
            Some(cast) => format!("({}.{}::{})", SOURCE_ALIAS, r.column, cast),
            None => format!("{}.{}", SOURCE_ALIAS, r.column),
        };
    }
    let literals: Vec<String> = r.keys.iter().map(|k| pg_literal(k)).collect();
    let text = if literals.len() == 1 {
        format!("({}.elem ->> {})", ARRAY_ALIAS, literals[0])
    } else {
        let last = literals.len() - 1;
        format!(
            "({}.elem -> {} ->> {})",
            ARRAY_ALIAS,
            literals[..last].join(" -> "),
            literals[last]
        )
    };
    let cast = match &r.cast {
        Some(c) => Some(c.as_str()),
        None => implicit_cast,
    };
    let cast = match cast {
        Some(c) if !c.is_empty() => c,
        _ => return text,
    };
    if safe_cast {
        if let Some(pattern) = safe_cast_pattern(cast) {
            return format!(
                "(CASE WHEN {} ~ {} THEN {}::{} END)",
                text,
                pg_literal(pattern),
                text,
                cast
            );
        }
    }
    format!("({}::{})", text, cast)
}

fn ref_name(r: &Ref) -> String {
    match r.keys.last() {
        Some(k) => k.clone(),
        None => r.column.clone(),
    }
}

/// Default output alias: last key / column, `<unit>_<name>`, `count`, `<func>_<name>`.
pub(crate) fn default_alias(expr: &Expr) -> String {
    match expr {
        Expr::CountStar => "count".to_string(),
        Expr::Ref(r) => ref_name(r),
        Expr::Bucket { unit, arg } => format!("{}_{}", unit, ref_name(arg)),
        Expr::Agg { func, arg, .. } => match arg.as_ref() {
            Expr::Bucket { unit, arg } => format!("{}_{}_{}", func, unit, ref_name(arg)),
            Expr::Ref(r) => format!("{}_{}", func, ref_name(r)),
            _ => func.clone(),
        },
    }
}

fn path_refs(expr: &Expr) -> Vec<&Ref> {
    match expr {
        Expr::CountStar => Vec::new(),
        Expr::Ref(r) => {
            if r.keys.is_empty() {
                Vec::new()
            } else {
                vec![r]
            }
        }
        Expr::Bucket { arg, .. } => {
            if arg.keys.is_empty() {
                Vec::new()
            } else {
                vec![arg]
            }
        }
        Expr::Agg { arg, .. } => path_refs(arg),
    }
}

/// Rust mirror of `_Planner` (jsonb_unnest.pyx, TASK-782).
pub(crate) struct Planner {
    pub cfg: Config,
    pub array_column: Option<String>,
    pub select_aliases: Vec<(String, Expr)>,
    pub select_sql: Vec<(String, String)>,
}

impl Planner {
    pub(crate) fn new(cfg: Config) -> Self {
        Planner {
            cfg,
            array_column: None,
            select_aliases: Vec::new(),
            select_sql: Vec::new(),
        }
    }

    /// Expand a bare config alias (precedence: select alias > config alias > parse).
    /// Returns the expression and the config alias name that was expanded, if any.
    pub(crate) fn resolve(&mut self, expr: Expr, text: &str) -> Result<(Expr, Option<String>), String> {
        let alias_expr = match &expr {
            Expr::Ref(r) if r.keys.is_empty() && r.cast.is_none() => self
                .cfg
                .alias(&r.column)
                .map(|value| (r.column.clone(), value.to_string())),
            _ => None,
        };
        if let Some((name, value)) = alias_expr {
            let expanded = parse_expr(&value)?;
            self.track(&expanded, text, true)?;
            return Ok((expanded, Some(name)));
        }
        self.track(&expr, text, false)?;
        Ok((expr, None))
    }

    /// Register every path column in `expr`; enforce single column, columns allowlist, strict.
    pub(crate) fn track(&mut self, expr: &Expr, text: &str, from_alias: bool) -> Result<(), String> {
        for r in path_refs(expr) {
            if self.cfg.strict && !from_alias {
                return Err(format!(
                    "jsonb_unnest: raw path '{}' not allowed in strict mode",
                    text
                ));
            }
            let column = &r.column;
            match &self.array_column {
                None => {
                    if let Some(columns) = &self.cfg.columns {
                        if !columns.iter().any(|(n, _)| n == column) {
                            return Err(format!(
                                "jsonb_unnest: array column '{}' is not declared in columns",
                                column
                            ));
                        }
                    }
                    self.array_column = Some(column.clone());
                }
                Some(first) => {
                    if first != column {
                        return Err(format!(
                            "jsonb_unnest: more than one array column ('{}', '{}')",
                            first, column
                        ));
                    }
                }
            }
        }
        Ok(())
    }

    /// Effective safe_cast for `column` (column value, else top-level).
    pub(crate) fn safe_cast_for(&self, column: &str) -> bool {
        if let Some(col) = self.cfg.column(column) {
            if let Some(value) = col.safe_cast {
                return value;
            }
        }
        self.cfg.safe_cast
    }

    /// Render the lateral clause (or "" without an array column).
    pub(crate) fn lateral(&self) -> String {
        let column = match &self.array_column {
            Some(c) => c,
            None => return String::new(),
        };
        let empty = match self.cfg.column(column) {
            Some(c) => c.empty.as_str(),
            None => "exclude",
        };
        let source = format!("{}.{}", SOURCE_ALIAS, column);
        let call = format!(
            "jsonb_array_elements(CASE jsonb_typeof({source}) WHEN 'array' THEN {source} ELSE '[]'::jsonb END) AS {alias}(elem)",
            source = source,
            alias = ARRAY_ALIAS
        );
        if empty == "include" {
            format!("LEFT JOIN LATERAL {} ON true", call)
        } else {
            format!("CROSS JOIN LATERAL {}", call)
        }
    }

    /// Render `expr` with this plan's effective safe_cast rules.
    pub(crate) fn render(&self, expr: &Expr) -> String {
        match expr {
            Expr::CountStar => "count(*)".to_string(),
            Expr::Ref(r) => render_ref(r, self.safe_cast_for(&r.column), None),
            Expr::Bucket { unit, arg } => {
                let arg_sql = render_ref(arg, self.safe_cast_for(&arg.column), Some("date"));
                format!("(date_trunc('{}', {})::date)", unit, arg_sql)
            }
            Expr::Agg {
                func,
                distinct,
                arg,
            } => {
                let arg_sql = match arg.as_ref() {
                    Expr::Ref(r) => {
                        let implicit = if func == "sum" || func == "avg" {
                            Some("numeric")
                        } else {
                            None
                        };
                        render_ref(r, self.safe_cast_for(&r.column), implicit)
                    }
                    other => self.render(other),
                };
                if *distinct {
                    format!("{}(DISTINCT {})", func, arg_sql)
                } else {
                    format!("{}({})", func, arg_sql)
                }
            }
        }
    }

    fn select_sql_for(&self, name: &str) -> Option<&str> {
        self.select_sql
            .iter()
            .find(|(n, _)| n == name)
            .map(|(_, s)| s.as_str())
    }

    /// Append `<expr> AS "<alias>"` rejecting duplicate output aliases.
    pub(crate) fn add_select(
        &mut self,
        select: &mut Vec<String>,
        expr: Expr,
        alias: &str,
    ) -> Result<(), String> {
        if self.select_aliases.iter().any(|(n, _)| n == alias) {
            return Err(format!("jsonb_unnest: duplicate output alias '{}'", alias));
        }
        let sql = self.render(&expr);
        select.push(format!("{} AS \"{}\"", sql, alias));
        self.select_aliases.push((alias.to_string(), expr));
        self.select_sql.push((alias.to_string(), sql));
        Ok(())
    }

    /// Render the conditions of one `having` entry.
    pub(crate) fn having_condition(&mut self, key: &str, value: &UValue) -> Result<Vec<String>, String> {
        let selected = self
            .select_aliases
            .iter()
            .find(|(n, e)| n == key && e.is_aggregate())
            .map(|_| self.select_sql_for(key).unwrap_or("").to_string());
        let sql = match selected {
            Some(sql) => sql,
            None => {
                let expr = match self.cfg.alias(key).map(|v| v.to_string()) {
                    Some(value) => {
                        let expr = parse_expr(&value)?;
                        self.track(&expr, key, true)?;
                        expr
                    }
                    None => {
                        let expr = parse_expr(key)?;
                        self.track(&expr, key, false)?;
                        expr
                    }
                };
                if !expr.is_aggregate() {
                    return Err(format!("jsonb_unnest: unknown having key '{}'", key));
                }
                self.render(&expr)
            }
        };
        let pairs: Vec<(String, &UValue)> = match value {
            UValue::Dict(entries) => entries.iter().map(|(k, v)| (k.clone(), v)).collect(),
            other => vec![("=".to_string(), other)],
        };
        let mut conditions = Vec::new();
        for (op, operand) in pairs {
            if !HAVING_OPERATORS.contains(&op.as_str()) {
                return Err(format!("jsonb_unnest: invalid having operator '{}'", op));
            }
            conditions.push(format!("{} {} {}", sql, op, having_value(key, operand)?));
        }
        Ok(conditions)
    }
}

/// Format a float like Python `repr(float)` for the common finite cases.
pub(crate) fn float_repr(value: f64) -> String {
    format!("{:?}", value)
}

fn having_value(key: &str, value: &UValue) -> Result<String, String> {
    let invalid = || format!("jsonb_unnest: invalid having value for '{}'", key);
    match value {
        UValue::Int(i) => Ok(i.to_string()),
        UValue::Float(f) => {
            if !f.is_finite() {
                return Err(invalid());
            }
            Ok(float_repr(*f))
        }
        UValue::Str(s) => Ok(pg_literal(s)),
        _ => Err(invalid()),
    }
}

// ---------------------------------------------------------------------------
// Plan
// ---------------------------------------------------------------------------

/// A row-filter entry of the plan: an original filter key (value passed through by the
/// pyfunction as the ORIGINAL Python object) or a pre-filter built by the planner.
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum RowEntry {
    Original(String),
    Prefilter(String, UValue),
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Plan {
    pub select: Vec<String>,
    pub group_by: Vec<String>,
    pub order_by: Vec<String>,
    pub having: Vec<String>,
    pub element_where: Vec<String>,
    pub lateral: String,
    pub row_filter: Vec<RowEntry>,
}

/// Port of `unnest_plan` WITHOUT element filters: every filter key -> `RowEntry::Original`.
pub(crate) fn build_plan_core(
    fields: &[String],
    grouping: &[String],
    ordering: &[String],
    filter: &[(String, UValue)],
    having: &UValue,
    config: &UValue,
) -> Result<Option<Plan>, String> {
    let filter_keys: Vec<String> = filter.iter().map(|(k, _)| k.clone()).collect();
    if !is_plan_candidate(fields, grouping, ordering, &filter_keys, having, config) {
        return Ok(None);
    }
    let mut planner = Planner::new(validate_config(config)?);
    let mut select: Vec<String> = Vec::new();
    for text in fields {
        let item = parse_select_item(text)?;
        let (expr, alias_name) = planner.resolve(item.expr, text)?;
        let alias = match item.alias.or(alias_name) {
            Some(a) => a,
            None => default_alias(&expr),
        };
        planner.add_select(&mut select, expr, &alias)?;
    }
    let mut group_by: Vec<String> = Vec::new();
    for text in grouping {
        let stripped = text.trim();
        if is_ident(stripped) {
            if let Some(sql) = planner.select_sql_for(stripped) {
                group_by.push(sql.to_string());
                continue;
            }
        }
        let (expr, alias_name) = planner.resolve(parse_expr(stripped)?, text)?;
        group_by.push(planner.render(&expr));
        if fields.is_empty() {
            let alias = match alias_name {
                Some(a) => a,
                None => default_alias(&expr),
            };
            planner.add_select(&mut select, expr, &alias)?;
        }
    }
    if fields.is_empty() {
        planner.add_select(&mut select, Expr::CountStar, "count")?;
    }
    let mut order_by: Vec<String> = Vec::new();
    for text in ordering {
        let item = parse_order_item(text)?;
        let mut rendered = match &item.name {
            Some(name) if planner.select_sql_for(name).is_some() => format!("\"{}\"", name),
            _ => {
                let (expr, _) = planner.resolve(item.expr.clone(), text)?;
                planner.render(&expr)
            }
        };
        if let Some(direction) = &item.direction {
            rendered = format!("{} {}", rendered, direction);
        }
        if let Some(nulls) = &item.nulls {
            rendered = format!("{} NULLS {}", rendered, nulls);
        }
        order_by.push(rendered);
    }
    let mut having_sql: Vec<String> = Vec::new();
    if having.truthy() {
        let entries = match having {
            UValue::Dict(d) => d,
            _ => return Err("jsonb_unnest: having must be a mapping".to_string()),
        };
        if !planner.select_aliases.iter().any(|(_, e)| e.is_aggregate()) {
            return Err("jsonb_unnest: having requires an aggregate".to_string());
        }
        for (key, value) in entries {
            having_sql.extend(planner.having_condition(key, value)?);
        }
    }
    let row_filter = filter
        .iter()
        .map(|(k, _)| RowEntry::Original(k.clone()))
        .collect();
    Ok(Some(Plan {
        select,
        group_by,
        order_by,
        having: having_sql,
        element_where: Vec::new(),
        lateral: planner.lateral(),
        row_filter,
    }))
}

/// Port of `unnest_wrap`.
pub(crate) fn wrap_sql(
    inner_sql: &str,
    select: &[String],
    lateral: &str,
    element_where: &[String],
) -> String {
    let mut sql = format!(
        "SELECT {} FROM ({}) AS {}",
        select.join(", "),
        inner_sql.trim(),
        SOURCE_ALIAS
    );
    if !lateral.is_empty() {
        sql = format!("{} {}", sql, lateral);
    }
    if !element_where.is_empty() {
        sql = format!("{} WHERE {}", sql, element_where.join(" AND "));
    }
    sql
}

fn plan_strings(plan: &Bound<'_, PyDict>, key: &str) -> PyResult<Vec<String>> {
    let invalid = || PyValueError::new_err("jsonb_unnest: invalid plan");
    plan.get_item(key)?
        .ok_or_else(invalid)?
        .extract::<Vec<String>>()
        .map_err(|_| invalid())
}

/// Python entry point: wrap `inner_sql` per a plan dict (keys `select`, `lateral`, `element_where`).
#[pyfunction]
#[pyo3(signature = (inner_sql, plan))]
pub fn pgsql_unnest_wrap(inner_sql: &str, plan: &Bound<'_, PyDict>) -> PyResult<String> {
    let invalid = || PyValueError::new_err("jsonb_unnest: invalid plan");
    let select = plan_strings(plan, "select")?;
    let element_where = plan_strings(plan, "element_where")?;
    let lateral: String = plan
        .get_item("lateral")?
        .ok_or_else(invalid)?
        .extract()
        .map_err(|_| invalid())?;
    Ok(wrap_sql(inner_sql, &select, &lateral, &element_where))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(items: &[&str]) -> Vec<String> {
        items.iter().map(|i| i.to_string()).collect()
    }

    fn d(entries: Vec<(&str, UValue)>) -> UValue {
        UValue::Dict(entries.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
    }

    fn plan(
        fields: &[&str],
        grouping: &[&str],
        ordering: &[&str],
        having: UValue,
        config: UValue,
    ) -> Result<Option<Plan>, String> {
        build_plan_core(&s(fields), &s(grouping), &s(ordering), &[], &having, &config)
    }

    const LATERAL: &str = "CROSS JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(_qs_src.graduation_details) WHEN 'array' THEN _qs_src.graduation_details ELSE '[]'::jsonb END) AS _qs_e0(elem)";
    const COURSE: &str = "(_qs_e0.elem ->> 'course')";

    #[test]
    fn test_parse_ref_path_cast() {
        let r = parse_ref("graduation_details[].course_date :: DATE").unwrap();
        assert_eq!(r.column, "graduation_details");
        assert_eq!(r.keys, vec!["course_date".to_string()]);
        assert_eq!(r.cast.as_deref(), Some("date"));
    }

    #[test]
    fn test_unknown_cast_message() {
        assert_eq!(
            parse_ref("a[].k::regclass").unwrap_err(),
            "jsonb_unnest: unknown cast 'regclass'"
        );
    }

    #[test]
    fn test_parse_ref_rejects() {
        for bad in ["a;drop", "a[].", "a[]", "a[].k k", "a[].k'", "a[]..k", "1col", "a[].b[].c", "a.b", ""] {
            assert_eq!(
                parse_ref(bad).unwrap_err(),
                format!("jsonb_unnest: invalid reference '{}'", bad)
            );
        }
    }

    #[test]
    fn test_parse_expr_forms() {
        assert_eq!(parse_expr("count(*)").unwrap(), Expr::CountStar);
        match parse_expr("COUNT(DISTINCT student_uid)").unwrap() {
            Expr::Agg { func, distinct, .. } => {
                assert_eq!(func, "count");
                assert!(distinct);
            }
            other => panic!("unexpected {:?}", other),
        }
        assert!(matches!(
            parse_expr("min(year(a[].d::date))").unwrap(),
            Expr::Agg { .. }
        ));
        assert!(matches!(parse_expr("month(a[].d)").unwrap(), Expr::Bucket { .. }));
    }

    #[test]
    fn test_parse_expr_rejects() {
        for bad in [
            "count(distinct *)",
            "sum(distinct x)",
            "max(count(*))",
            "foo(x)",
            "year(sum(x))",
            "count()",
            "count(distinct)",
        ] {
            assert_eq!(
                parse_expr(bad).unwrap_err(),
                format!("jsonb_unnest: invalid expression '{}'", bad)
            );
        }
        assert_eq!(
            parse_expr("sum(a[].n::foo)").unwrap_err(),
            "jsonb_unnest: unknown cast 'foo'"
        );
    }

    #[test]
    fn test_select_and_order_items() {
        let item = parse_select_item("count(distinct student_uid) as graduates").unwrap();
        assert_eq!(item.alias.as_deref(), Some("graduates"));
        assert_eq!(
            parse_select_item("x as 1x").unwrap_err(),
            "jsonb_unnest: invalid select item 'x as 1x'"
        );
        let item = parse_order_item("a[].k asc nulls last").unwrap();
        assert_eq!(item.direction.as_deref(), Some("ASC"));
        assert_eq!(item.nulls.as_deref(), Some("LAST"));
        assert_eq!(item.name, None);
        let item = parse_order_item("graduates DESC").unwrap();
        assert_eq!(item.name.as_deref(), Some("graduates"));
        assert_eq!(
            parse_order_item("x DESC DESC").unwrap_err(),
            "jsonb_unnest: invalid order item 'x DESC DESC'"
        );
    }

    #[test]
    fn test_validate_config_defaults_and_errors() {
        let cfg = validate_config(&UValue::None).unwrap();
        assert!(cfg.columns.is_none() && cfg.aliases.is_empty() && !cfg.strict && !cfg.safe_cast);
        assert_eq!(
            validate_config(&UValue::List(vec![])).unwrap_err(),
            "jsonb_unnest: invalid config: must be a mapping"
        );
        assert_eq!(
            validate_config(&d(vec![("bogus", UValue::Int(1))])).unwrap_err(),
            "jsonb_unnest: invalid config: unknown key 'bogus'"
        );
        assert_eq!(
            validate_config(&d(vec![("strict", UValue::Int(1))])).unwrap_err(),
            "jsonb_unnest: invalid config: strict must be a boolean"
        );
        assert_eq!(
            validate_config(&d(vec![(
                "columns",
                d(vec![("a", d(vec![("empty", UValue::Str("maybe".into()))]))])
            )]))
            .unwrap_err(),
            "jsonb_unnest: invalid config: empty must be 'exclude' or 'include'"
        );
        assert_eq!(
            validate_config(&d(vec![("aliases", d(vec![("a", UValue::Str("foo(x)".into()))]))]))
                .unwrap_err(),
            "jsonb_unnest: invalid expression 'foo(x)'"
        );
    }

    #[test]
    fn test_plan_candidate() {
        let none = UValue::None;
        assert!(is_plan_candidate(&s(&["g[].k"]), &[], &[], &[], &none, &none));
        assert!(is_plan_candidate(&[], &[], &[], &[], &d(vec![("n", UValue::Int(1))]), &none));
        assert!(!is_plan_candidate(&s(&["tags::text[]", "id"]), &[], &[], &[], &d(vec![]), &none));
        let cfg = d(vec![("aliases", d(vec![("course", UValue::Str("g[].course".into()))]))]);
        assert!(is_plan_candidate(&[], &[], &s(&["course DESC"]), &[], &none, &cfg));
        assert!(is_plan_candidate(&[], &[], &[], &s(&["course!"]), &none, &cfg));
        assert!(!is_plan_candidate(&s(&["course"]), &[], &[], &[], &none, &none));
    }

    #[test]
    fn test_spec_example_plan() {
        let p = plan(
            &[
                "graduation_details[].course",
                "graduation_details[].category",
                "count(distinct student_uid) as graduates",
            ],
            &["graduation_details[].course", "graduation_details[].category"],
            &["graduates DESC"],
            d(vec![("graduates", d(vec![(">", UValue::Int(5))]))]),
            UValue::None,
        )
        .unwrap()
        .unwrap();
        assert_eq!(
            p.select,
            vec![
                "(_qs_e0.elem ->> 'course') AS \"course\"".to_string(),
                "(_qs_e0.elem ->> 'category') AS \"category\"".to_string(),
                "count(DISTINCT _qs_src.student_uid) AS \"graduates\"".to_string(),
            ]
        );
        assert_eq!(
            p.group_by,
            vec![COURSE.to_string(), "(_qs_e0.elem ->> 'category')".to_string()]
        );
        assert_eq!(p.order_by, vec!["\"graduates\" DESC".to_string()]);
        assert_eq!(p.having, vec!["count(DISTINCT _qs_src.student_uid) > 5".to_string()]);
        assert_eq!(p.lateral, LATERAL);
    }

    #[test]
    fn test_not_a_candidate() {
        assert_eq!(plan(&["a", "b"], &["a"], &[], UValue::None, UValue::None).unwrap(), None);
    }

    #[test]
    fn test_path_rendering() {
        let p = plan(
            &["graduation_details[].meta.level", "graduation_details[].course_date::date", "created_at::date", "student_uid"],
            &[],
            &[],
            UValue::None,
            UValue::None,
        )
        .unwrap()
        .unwrap();
        assert_eq!(p.select[0], "(_qs_e0.elem -> 'meta' ->> 'level') AS \"level\"");
        assert_eq!(p.select[1], "((_qs_e0.elem ->> 'course_date')::date) AS \"course_date\"");
        assert_eq!(p.select[2], "(_qs_src.created_at::date) AS \"created_at\"");
        assert_eq!(p.select[3], "_qs_src.student_uid AS \"student_uid\"");
    }

    #[test]
    fn test_buckets_and_aggregates() {
        let p = plan(
            &[
                "month(graduation_details[].course_date)",
                "day(created_at)",
                "count(*)",
                "sum(graduation_details[].points)",
                "avg(graduation_details[].points::int)",
                "min(year(graduation_details[].course_date))",
            ],
            &[],
            &[],
            UValue::None,
            UValue::None,
        )
        .unwrap()
        .unwrap();
        assert_eq!(
            p.select,
            vec![
                "(date_trunc('month', ((_qs_e0.elem ->> 'course_date')::date))::date) AS \"month_course_date\"".to_string(),
                "(date_trunc('day', _qs_src.created_at)::date) AS \"day_created_at\"".to_string(),
                "count(*) AS \"count\"".to_string(),
                "sum(((_qs_e0.elem ->> 'points')::numeric)) AS \"sum_points\"".to_string(),
                "avg(((_qs_e0.elem ->> 'points')::int)) AS \"avg_points\"".to_string(),
                "min((date_trunc('year', ((_qs_e0.elem ->> 'course_date')::date))::date)) AS \"min_year_course_date\"".to_string(),
            ]
        );
    }

    #[test]
    fn test_duplicate_alias_and_empty_fields() {
        assert_eq!(
            plan(&["count(*)", "count(*)", "g[].x"], &[], &[], UValue::None, UValue::None).unwrap_err(),
            "jsonb_unnest: duplicate output alias 'count'"
        );
        let p = plan(&[], &["graduation_details[].course", "licensee"], &[], UValue::None, UValue::None)
            .unwrap()
            .unwrap();
        assert_eq!(
            p.select,
            vec![
                format!("{} AS \"course\"", COURSE),
                "_qs_src.licensee AS \"licensee\"".to_string(),
                "count(*) AS \"count\"".to_string(),
            ]
        );
        assert_eq!(p.group_by, vec![COURSE.to_string(), "_qs_src.licensee".to_string()]);
    }

    #[test]
    fn test_order_by_variants() {
        let p = plan(
            &["graduation_details[].course", "count(*) as n"],
            &["graduation_details[].course"],
            &["n desc nulls last", "graduation_details[].course_date asc", "course", "licensee NULLS FIRST"],
            UValue::None,
            UValue::None,
        )
        .unwrap()
        .unwrap();
        assert_eq!(
            p.order_by,
            vec![
                "\"n\" DESC NULLS LAST".to_string(),
                "(_qs_e0.elem ->> 'course_date') ASC".to_string(),
                "\"course\"".to_string(),
                "_qs_src.licensee NULLS FIRST".to_string(),
            ]
        );
    }

    fn alias_config() -> UValue {
        d(vec![
            ("columns", d(vec![("graduation_details", d(vec![("empty", UValue::Str("exclude".into()))]))])),
            (
                "aliases",
                d(vec![
                    ("course", UValue::Str("graduation_details[].course".into())),
                    ("diploma_year", UValue::Str("year(graduation_details[].course_date::date)".into())),
                ]),
            ),
        ])
    }

    #[test]
    fn test_config_aliases_strict_columns() {
        let p = plan(&["course", "diploma_year", "count(*)"], &["course", "diploma_year"], &["diploma_year DESC"], UValue::None, alias_config())
            .unwrap()
            .unwrap();
        let year = "(date_trunc('year', ((_qs_e0.elem ->> 'course_date')::date))::date)";
        assert_eq!(p.select[1], format!("{} AS \"diploma_year\"", year));
        assert_eq!(p.group_by, vec![COURSE.to_string(), year.to_string()]);
        assert_eq!(p.order_by, vec!["\"diploma_year\" DESC".to_string()]);

        let strict = d(vec![
            ("strict", UValue::Bool(true)),
            ("aliases", d(vec![("course", UValue::Str("g[].course".into()))])),
        ]);
        assert_eq!(
            plan(&["g[].course"], &[], &[], UValue::None, strict.clone()).unwrap_err(),
            "jsonb_unnest: raw path 'g[].course' not allowed in strict mode"
        );
        assert!(plan(&["course"], &[], &[], UValue::None, strict).unwrap().is_some());
        assert_eq!(
            plan(&["other[].k"], &[], &[], UValue::None, alias_config()).unwrap_err(),
            "jsonb_unnest: array column 'other' is not declared in columns"
        );
        assert_eq!(
            plan(&["a[].k", "b[].k"], &[], &[], UValue::None, UValue::None).unwrap_err(),
            "jsonb_unnest: more than one array column ('a', 'b')"
        );
    }

    #[test]
    fn test_empty_include_and_safe_cast() {
        let cfg = d(vec![("columns", d(vec![("graduation_details", d(vec![("empty", UValue::Str("include".into()))]))]))]);
        let p = plan(&["graduation_details[].course"], &[], &[], UValue::None, cfg).unwrap().unwrap();
        assert!(p.lateral.starts_with("LEFT JOIN LATERAL ") && p.lateral.ends_with(" ON true"));

        let cfg = d(vec![("safe_cast", UValue::Bool(true))]);
        let p = plan(&["g[].k::date", "g[].n::text", "sum(g[].v)"], &[], &[], UValue::None, cfg).unwrap().unwrap();
        assert_eq!(
            p.select[0],
            "(CASE WHEN (_qs_e0.elem ->> 'k') ~ '^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN (_qs_e0.elem ->> 'k')::date END) AS \"k\""
        );
        assert_eq!(p.select[1], "((_qs_e0.elem ->> 'n')::text) AS \"n\"");
        assert!(p.select[2].contains("CASE WHEN (_qs_e0.elem ->> 'v') ~ '^-?[0-9]+([.][0-9]+)?$' THEN"));
    }

    #[test]
    fn test_having() {
        let p = plan(
            &["g[].course", "count(*) as n", "max(g[].category) as top"],
            &[],
            &[],
            d(vec![
                ("n", d(vec![(">=", UValue::Int(2)), ("<", UValue::Float(10.5))])),
                ("top", UValue::Str("Comprehensive".into())),
                ("count(*)", UValue::Int(3)),
            ]),
            UValue::None,
        )
        .unwrap()
        .unwrap();
        assert_eq!(
            p.having,
            vec![
                "count(*) >= 2".to_string(),
                "count(*) < 10.5".to_string(),
                "max((_qs_e0.elem ->> 'category')) = 'Comprehensive'".to_string(),
                "count(*) = 3".to_string(),
            ]
        );
        let base = |having: UValue| plan(&["licensee", "count(*) as n"], &[], &[], having, UValue::None);
        assert_eq!(base(UValue::Str("x".into())).unwrap_err(), "jsonb_unnest: having must be a mapping");
        assert_eq!(
            base(d(vec![("licensee", UValue::Int(1))])).unwrap_err(),
            "jsonb_unnest: unknown having key 'licensee'"
        );
        assert_eq!(
            base(d(vec![("n", d(vec![("~", UValue::Int(1))]))])).unwrap_err(),
            "jsonb_unnest: invalid having operator '~'"
        );
        assert_eq!(
            base(d(vec![("n", UValue::Bool(true))])).unwrap_err(),
            "jsonb_unnest: invalid having value for 'n'"
        );
        assert_eq!(
            base(d(vec![("n", UValue::List(vec![]))])).unwrap_err(),
            "jsonb_unnest: invalid having value for 'n'"
        );
        assert_eq!(
            plan(&["licensee"], &[], &[], d(vec![("licensee", UValue::Int(1))]), UValue::None).unwrap_err(),
            "jsonb_unnest: having requires an aggregate"
        );
        let p = plan(&["licensee", "max(name) as m"], &[], &[], d(vec![("m", UValue::Str("a{b}".into()))]), UValue::None)
            .unwrap()
            .unwrap();
        assert_eq!(p.having, vec!["max(_qs_src.name) = E'a\\x7bb\\x7d'".to_string()]);
        assert_eq!(p.lateral, "");
    }

    #[test]
    fn test_wrap_sql() {
        assert_eq!(
            wrap_sql("  SELECT 1  ", &s(&["a AS \"a\""]), "", &[]),
            "SELECT a AS \"a\" FROM (SELECT 1) AS _qs_src"
        );
        assert_eq!(
            wrap_sql("SELECT 1", &s(&["a AS \"a\""]), "CROSS JOIN LATERAL x", &s(&["p", "q"])),
            "SELECT a AS \"a\" FROM (SELECT 1) AS _qs_src CROSS JOIN LATERAL x WHERE p AND q"
        );
    }
}
