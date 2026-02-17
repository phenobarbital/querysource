// Copyright (C) 2018-present Jesus Lara
//
// arangodb_parser.rs — ArangoDB AQL query builder with rayon parallelism.
// Builds AQL string queries from Python dicts/lists via PyO3.
// Follows the 3-phase pattern: extract (GIL) → process (rayon) → convert (GIL).

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString};
use rayon::prelude::*;
use regex::Regex;
use once_cell::sync::Lazy;

// ---------------------------------------------------------------------------
// Shared types for parallel processing
// ---------------------------------------------------------------------------

/// Represents a single AQL filter entry (GIL-free).
#[derive(Debug, Clone)]
struct AqlFilterEntry {
    field_name: String,
    suffix: String,
    value: AqlValue,
    field_type: Option<String>,
}

/// Possible value types from a Python filter dict.
#[derive(Debug, Clone)]
enum AqlValue {
    Str(String),
    Int(i64),
    Float(f64),
    Bool(bool),
    None,
    /// {operator: value}  e.g. {">=": 18}
    Dict { op: String, val: Box<AqlValue> },
    /// Plain list of values
    List(Vec<AqlValue>),
}

/// A processed AQL FILTER clause fragment (GIL-free).
#[derive(Debug, Clone)]
enum AqlCondition {
    /// doc.field == value
    Eq(String, String),
    /// doc.field op value  (e.g. doc.age >= 18)
    Op(String, String, String),
    /// doc.field >= low AND doc.field <= high
    Range(String, String, String),
    /// doc.field == null  or  doc.field != null
    NullCheck(String, bool),
    /// doc.field IN [values]  or  doc.field NOT IN [values]
    InList(String, bool, Vec<String>),
    /// LIKE: doc.field LIKE pattern
    Like(String, String),
}

/// Parsed ordering entry (GIL-free).
#[derive(Debug, Clone)]
struct AqlOrderEntry {
    field: String,
    direction: String,
}

/// Graph traversal options (GIL-free).
#[derive(Debug, Clone)]
struct GraphOptions {
    direction: String,
    start_vertex: String,
    edge_collection: String,
    min_depth: u32,
    max_depth: u32,
}

/// Search options (GIL-free).
#[derive(Debug, Clone)]
struct SearchOptions {
    view: String,
    analyzer: Option<String>,
    fields: Vec<(String, String)>,
    phrase: Vec<(String, String)>,
}

// Regex for BETWEEN parsing
static BETWEEN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)BETWEEN\s+(\S+)\s+AND\s+(\S+)").unwrap()
});

// ---------------------------------------------------------------------------
// SQL→AQL operator mapping
// ---------------------------------------------------------------------------

fn sql_to_aql_op(op: &str) -> Option<&'static str> {
    match op {
        "=" => Some("=="),
        ">" => Some(">"),
        ">=" => Some(">="),
        "<" => Some("<"),
        "<=" => Some("<="),
        "<>" | "!=" => Some("!="),
        "==" => Some("=="),
        "LIKE" | "like" => Some("LIKE"),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// Value formatting for AQL
// ---------------------------------------------------------------------------

fn format_aql_value(val: &AqlValue, field_type: &Option<String>) -> String {
    let converted = convert_value(val, field_type);
    match &converted {
        AqlValue::Str(s) => {
            // Check for special AQL values that should not be quoted
            let upper = s.to_uppercase();
            if upper == "NULL" || upper == "TRUE" || upper == "FALSE" {
                upper
            } else {
                format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
            }
        }
        AqlValue::Int(i) => i.to_string(),
        AqlValue::Float(f) => f.to_string(),
        AqlValue::Bool(b) => if *b { "true".to_string() } else { "false".to_string() },
        AqlValue::None => "null".to_string(),
        _ => "null".to_string(),
    }
}

/// Convert a value based on field_type hint (pure Rust, no GIL).
fn convert_value(value: &AqlValue, field_type: &Option<String>) -> AqlValue {
    match field_type.as_deref() {
        Some("string") => match value {
            AqlValue::Int(i) => AqlValue::Str(i.to_string()),
            AqlValue::Float(f) => AqlValue::Str(f.to_string()),
            AqlValue::Bool(b) => AqlValue::Str(b.to_string()),
            _ => value.clone(),
        },
        Some("integer") => match value {
            AqlValue::Str(s) => s.parse::<i64>().map_or(value.clone(), AqlValue::Int),
            _ => value.clone(),
        },
        Some("float") => match value {
            AqlValue::Str(s) => s.parse::<f64>().map_or(value.clone(), AqlValue::Float),
            _ => value.clone(),
        },
        Some("boolean") => match value {
            AqlValue::Str(s) => {
                let lower = s.to_lowercase();
                AqlValue::Bool(matches!(lower.as_str(), "true" | "yes" | "1"))
            }
            _ => value.clone(),
        },
        _ => value.clone(),
    }
}

// ---------------------------------------------------------------------------
// Phase 1: Extract from Python (serial, holds GIL)
// ---------------------------------------------------------------------------

fn extract_aql_value(py_val: &Bound<'_, PyAny>) -> AqlValue {
    if py_val.is_none() {
        return AqlValue::None;
    }
    // Bool before int (Python bool is int subclass)
    if let Ok(b) = py_val.extract::<bool>() {
        return AqlValue::Bool(b);
    }
    if let Ok(i) = py_val.extract::<i64>() {
        return AqlValue::Int(i);
    }
    if let Ok(f) = py_val.extract::<f64>() {
        return AqlValue::Float(f);
    }
    if let Ok(s) = py_val.extract::<String>() {
        return AqlValue::Str(s);
    }
    // Dict: extract first key-value pair as {operator: value}
    if let Ok(d) = py_val.downcast::<PyDict>() {
        if let Some((k, v)) = d.iter().next() {
            if let (Ok(op), val) = (k.extract::<String>(), extract_aql_value(&v)) {
                return AqlValue::Dict {
                    op,
                    val: Box::new(val),
                };
            }
        }
        return AqlValue::None;
    }
    // List
    if let Ok(list) = py_val.downcast::<PyList>() {
        let items: Vec<AqlValue> = list.iter().map(|item| extract_aql_value(&item)).collect();
        return AqlValue::List(items);
    }
    AqlValue::None
}

fn extract_filter_entries(
    filter_dict: &Bound<'_, PyDict>,
    cond_definition: &Bound<'_, PyDict>,
) -> Vec<AqlFilterEntry> {
    filter_dict
        .iter()
        .filter_map(|(py_key, py_val)| {
            let key: String = py_key.extract().ok()?;
            let (field_name, suffix) = parse_field_key(&key);
            let field_type: Option<String> = cond_definition
                .get_item(&key)
                .ok()
                .flatten()
                .and_then(|v| v.extract().ok());
            let value = extract_aql_value(&py_val);
            Some(AqlFilterEntry {
                field_name,
                suffix,
                value,
                field_type,
            })
        })
        .collect()
}

/// Extract graph options from a Python dict. Returns None if empty or missing.
fn extract_graph_options(graph_dict: &Bound<'_, PyDict>) -> Option<GraphOptions> {
    let direction: String = graph_dict
        .get_item("direction")
        .ok()
        .flatten()
        .and_then(|v| v.extract().ok())
        .unwrap_or_else(|| "OUTBOUND".to_string());

    let start_vertex: String = graph_dict
        .get_item("start_vertex")
        .ok()
        .flatten()
        .and_then(|v| v.extract().ok())?;

    let edge_collection: String = graph_dict
        .get_item("edge_collection")
        .ok()
        .flatten()
        .and_then(|v| v.extract().ok())?;

    let min_depth: u32 = graph_dict
        .get_item("min_depth")
        .ok()
        .flatten()
        .and_then(|v| v.extract().ok())
        .unwrap_or(1);

    let max_depth: u32 = graph_dict
        .get_item("max_depth")
        .ok()
        .flatten()
        .and_then(|v| v.extract().ok())
        .unwrap_or(1);

    Some(GraphOptions {
        direction: direction.to_uppercase(),
        start_vertex,
        edge_collection,
        min_depth,
        max_depth,
    })
}

/// Extract search options from a Python dict. Returns None if empty or missing.
fn extract_search_options(search_dict: &Bound<'_, PyDict>) -> Option<SearchOptions> {
    let view: String = search_dict
        .get_item("view")
        .ok()
        .flatten()
        .and_then(|v| v.extract().ok())?;

    let analyzer: Option<String> = search_dict
        .get_item("analyzer")
        .ok()
        .flatten()
        .and_then(|v| v.extract().ok());

    let mut fields: Vec<(String, String)> = Vec::new();
    if let Ok(Some(fields_dict)) = search_dict.get_item("fields") {
        if let Ok(d) = fields_dict.downcast::<PyDict>() {
            for (k, v) in d.iter() {
                if let (Ok(key), Ok(val)) = (k.extract::<String>(), v.extract::<String>()) {
                    fields.push((key, val));
                }
            }
        }
    }

    let mut phrase: Vec<(String, String)> = Vec::new();
    if let Ok(Some(phrase_dict)) = search_dict.get_item("phrase") {
        if let Ok(d) = phrase_dict.downcast::<PyDict>() {
            for (k, v) in d.iter() {
                if let (Ok(key), Ok(val)) = (k.extract::<String>(), v.extract::<String>()) {
                    phrase.push((key, val));
                }
            }
        }
    }

    Some(SearchOptions {
        view,
        analyzer,
        fields,
        phrase,
    })
}

// ---------------------------------------------------------------------------
// Field key parsing
// ---------------------------------------------------------------------------

fn parse_field_key(key: &str) -> (String, String) {
    if let Some(stripped) = key.strip_suffix('!') {
        (stripped.to_string(), "!".to_string())
    } else {
        (key.to_string(), String::new())
    }
}

// ---------------------------------------------------------------------------
// Phase 2: Process entries in parallel (no GIL, pure Rust)
// ---------------------------------------------------------------------------

fn process_aql_entry(entry: &AqlFilterEntry, doc_var: &str) -> Option<AqlCondition> {
    let ft = &entry.field_type;
    let field = &entry.field_name;
    let doc_field = format!("{}.{}", doc_var, field);

    match &entry.value {
        AqlValue::Dict { op, val } => {
            if op.to_uppercase() == "LIKE" || op.to_lowercase() == "like" {
                let formatted = format_aql_value(val, ft);
                Some(AqlCondition::Like(doc_field, formatted))
            } else if let Some(aql_op) = sql_to_aql_op(op) {
                let formatted = format_aql_value(val, ft);
                Some(AqlCondition::Op(doc_field, aql_op.to_string(), formatted))
            } else {
                // Assume the op is already an AQL operator
                let formatted = format_aql_value(val, ft);
                Some(AqlCondition::Op(doc_field, op.clone(), formatted))
            }
        }
        AqlValue::List(items) => {
            let formatted: Vec<String> = items.iter().map(|v| format_aql_value(v, ft)).collect();
            let negated = entry.suffix == "!";
            Some(AqlCondition::InList(doc_field, negated, formatted))
        }
        AqlValue::Str(s) => {
            let upper = s.to_uppercase();
            if upper == "NULL" || upper == "NONE" {
                Some(AqlCondition::NullCheck(doc_field, true))
            } else if upper == "!NULL" || upper == "!NONE" {
                Some(AqlCondition::NullCheck(doc_field, false))
            } else if upper.contains("BETWEEN") {
                if let Some(caps) = BETWEEN_RE.captures(s) {
                    let low = format_aql_value(&AqlValue::Str(caps[1].to_string()), ft);
                    let high = format_aql_value(&AqlValue::Str(caps[2].to_string()), ft);
                    Some(AqlCondition::Range(doc_field, low, high))
                } else {
                    None
                }
            } else if let Some(stripped) = s.strip_prefix('!') {
                let val = format_aql_value(&AqlValue::Str(stripped.to_string()), ft);
                Some(AqlCondition::Op(doc_field, "!=".to_string(), val))
            } else if s.contains('%') {
                // LIKE pattern
                let val = format_aql_value(&AqlValue::Str(s.clone()), ft);
                Some(AqlCondition::Like(doc_field, val))
            } else {
                let val = format_aql_value(&AqlValue::Str(s.clone()), ft);
                Some(AqlCondition::Eq(doc_field, val))
            }
        }
        AqlValue::Bool(b) => {
            let val = if *b { "true" } else { "false" };
            Some(AqlCondition::Eq(doc_field, val.to_string()))
        }
        AqlValue::Int(i) => {
            let val = format_aql_value(&AqlValue::Int(*i), ft);
            Some(AqlCondition::Eq(doc_field, val))
        }
        AqlValue::Float(f) => {
            let val = format_aql_value(&AqlValue::Float(*f), ft);
            Some(AqlCondition::Eq(doc_field, val))
        }
        AqlValue::None => Some(AqlCondition::NullCheck(doc_field, true)),
    }
}

// ---------------------------------------------------------------------------
// Phase 3: Render AQL condition to string
// ---------------------------------------------------------------------------

fn render_condition(cond: &AqlCondition) -> String {
    match cond {
        AqlCondition::Eq(field, val) => format!("{} == {}", field, val),
        AqlCondition::Op(field, op, val) => format!("{} {} {}", field, op, val),
        AqlCondition::Range(field, low, high) => {
            format!("{} >= {} AND {} <= {}", field, low, field, high)
        }
        AqlCondition::NullCheck(field, is_null) => {
            if *is_null {
                format!("{} == null", field)
            } else {
                format!("{} != null", field)
            }
        }
        AqlCondition::InList(field, negated, items) => {
            let list_str = format!("[{}]", items.join(", "));
            if *negated {
                format!("{} NOT IN {}", field, list_str)
            } else {
                format!("{} IN {}", field, list_str)
            }
        }
        AqlCondition::Like(field, pattern) => {
            format!("LIKE({}, {})", field, pattern)
        }
    }
}

// ---------------------------------------------------------------------------
// Public PyO3 functions
// ---------------------------------------------------------------------------

/// Build AQL FILTER clause strings from a filter dict with rayon parallel processing.
///
/// Returns a list of FILTER clause strings (without the FILTER keyword).
/// Each string is a single condition like `doc.age >= 18`.
#[pyfunction]
#[pyo3(signature = (filter_dict, cond_definition, doc_var="doc"))]
pub fn aql_filter_conditions(
    py: Python,
    filter_dict: &Bound<'_, PyDict>,
    cond_definition: &Bound<'_, PyDict>,
    doc_var: &str,
) -> PyResult<PyObject> {
    let result = PyList::empty(py);

    if filter_dict.is_empty() {
        return Ok(result.into_pyobject(py).unwrap().into_any().unbind());
    }

    // Phase 1: Extract (serial, GIL)
    let entries = extract_filter_entries(filter_dict, cond_definition);

    // Phase 2: Process in parallel (no GIL)
    let conditions: Vec<AqlCondition> = entries
        .par_iter()
        .filter_map(|e| process_aql_entry(e, doc_var))
        .collect();

    // Phase 3: Render to strings (serial, GIL)
    for cond in &conditions {
        let clause = render_condition(cond);
        result.append(PyString::new(py, &clause))?;
    }

    Ok(result.into_pyobject(py).unwrap().into_any().unbind())
}

/// Build AQL RETURN projection from a list of field names.
///
/// If empty, returns "RETURN doc" (all fields).
/// Otherwise returns "RETURN { field1: doc.field1, field2: doc.field2, ... }".
#[pyfunction]
#[pyo3(signature = (fields, doc_var="doc"))]
pub fn aql_process_fields(
    _py: Python,
    fields: Vec<String>,
    doc_var: &str,
) -> PyResult<String> {
    if fields.is_empty() {
        return Ok(format!("RETURN {}", doc_var));
    }

    // Process field mappings in parallel
    let field_parts: Vec<String> = fields
        .par_iter()
        .map(|field| {
            let trimmed = field.trim();
            // Handle "field as alias" syntax
            let lower = trimmed.to_lowercase();
            if let Some(pos) = lower.find(" as ") {
                let name = trimmed[..pos].trim();
                let alias = trimmed[pos + 4..].trim().replace('"', "");
                format!("{}: {}.{}", alias, doc_var, name)
            } else {
                format!("{}: {}.{}", trimmed, doc_var, trimmed)
            }
        })
        .collect();

    Ok(format!("RETURN {{ {} }}", field_parts.join(", ")))
}

/// Build AQL SORT clause from ordering specification.
///
/// Input: list of strings like ["name ASC", "-created_at", "age DESC"]
/// Output: "SORT doc.name ASC, doc.created_at DESC, doc.age DESC"
/// Returns empty string if no ordering.
#[pyfunction]
#[pyo3(signature = (ordering, doc_var="doc"))]
pub fn aql_process_ordering(
    _py: Python,
    ordering: Vec<String>,
    doc_var: &str,
) -> PyResult<String> {
    if ordering.is_empty() {
        return Ok(String::new());
    }

    // Process ordering in parallel
    let order_parts: Vec<AqlOrderEntry> = ordering
        .par_iter()
        .map(|item| {
            let trimmed = item.trim();
            if let Some(field) = trimmed.strip_prefix('-') {
                AqlOrderEntry {
                    field: field.to_string(),
                    direction: "DESC".to_string(),
                }
            } else {
                let parts: Vec<&str> = trimmed.splitn(2, ' ').collect();
                if parts.len() == 2 {
                    let dir_upper = parts[1].trim().to_uppercase();
                    let direction = if dir_upper == "DESC" {
                        "DESC".to_string()
                    } else {
                        "ASC".to_string()
                    };
                    AqlOrderEntry {
                        field: parts[0].to_string(),
                        direction,
                    }
                } else {
                    AqlOrderEntry {
                        field: trimmed.to_string(),
                        direction: "ASC".to_string(),
                    }
                }
            }
        })
        .collect();

    let sort_clauses: Vec<String> = order_parts
        .iter()
        .map(|e| format!("{}.{} {}", doc_var, e.field, e.direction))
        .collect();

    Ok(format!("SORT {}", sort_clauses.join(", ")))
}

/// Build a complete AQL query string.
///
/// Assembles FOR, FILTER, SEARCH, SORT, LIMIT, and RETURN clauses.
/// Supports optional graph traversal and ArangoSearch view queries.
///
/// Arguments:
///   collection: collection name (required)
///   filter_dict: filter conditions dict
///   cond_definition: field type hints
///   fields: list of field names for projection
///   ordering: list of ordering specs
///   grouping: list of group-by fields
///   limit: query limit (0 = no limit)
///   offset: query offset (0 = no offset)
///   doc_var: document variable name (default "doc")
///   graph: optional graph traversal dict
///   search: optional ArangoSearch dict
#[pyfunction]
#[pyo3(signature = (
    collection,
    filter_dict,
    cond_definition,
    fields,
    ordering,
    grouping,
    limit=0,
    offset=0,
    doc_var="doc",
    graph=None,
    search=None,
))]
#[allow(clippy::too_many_arguments)]
pub fn aql_build_query(
    py: Python,
    collection: &str,
    filter_dict: &Bound<'_, PyDict>,
    cond_definition: &Bound<'_, PyDict>,
    fields: Vec<String>,
    ordering: Vec<String>,
    grouping: Vec<String>,
    limit: i64,
    offset: i64,
    doc_var: &str,
    graph: Option<&Bound<'_, PyDict>>,
    search: Option<&Bound<'_, PyDict>>,
) -> PyResult<String> {
    let mut parts: Vec<String> = Vec::new();

    // --- Graph traversal or Search or standard FOR ---
    let search_opts = search.and_then(|s| extract_search_options(s));
    let graph_opts = graph.and_then(|g| extract_graph_options(g));

    if let Some(ref gopts) = graph_opts {
        // Graph traversal query
        parts.push(format!(
            "FOR v, e, p IN {}..{} {} '{}' {}",
            gopts.min_depth,
            gopts.max_depth,
            gopts.direction,
            gopts.start_vertex,
            gopts.edge_collection,
        ));
    } else if let Some(ref sopts) = search_opts {
        // ArangoSearch view query
        parts.push(format!("FOR {} IN {}", doc_var, sopts.view));

        // Build SEARCH clause
        let mut search_conditions: Vec<String> = Vec::new();
        for (field, value) in &sopts.fields {
            let escaped = value.replace('\\', "\\\\").replace('"', "\\\"");
            search_conditions.push(format!("{}.{} == \"{}\"", doc_var, field, escaped));
        }
        for (field, value) in &sopts.phrase {
            let escaped = value.replace('\\', "\\\\").replace('"', "\\\"");
            search_conditions.push(format!(
                "PHRASE({}.{}, \"{}\")",
                doc_var, field, escaped
            ));
        }

        if !search_conditions.is_empty() {
            let joined = search_conditions.join(" AND ");
            if let Some(ref analyzer) = sopts.analyzer {
                parts.push(format!("SEARCH ANALYZER({}, \"{}\")", joined, analyzer));
            } else {
                parts.push(format!("SEARCH {}", joined));
            }
        }
    } else {
        // Standard collection query
        parts.push(format!("FOR {} IN {}", doc_var, collection));
    }

    // --- FILTER ---
    if !filter_dict.is_empty() {
        let entries = extract_filter_entries(filter_dict, cond_definition);
        let effective_doc_var = if graph_opts.is_some() { "v" } else { doc_var };
        let conditions: Vec<AqlCondition> = entries
            .par_iter()
            .filter_map(|e| process_aql_entry(e, effective_doc_var))
            .collect();
        for cond in &conditions {
            parts.push(format!("FILTER {}", render_condition(cond)));
        }
    }

    // --- COLLECT (GROUP BY) ---
    if !grouping.is_empty() {
        let effective_doc_var = if graph_opts.is_some() { "v" } else { doc_var };
        let collect_parts: Vec<String> = grouping
            .iter()
            .map(|g| format!("{} = {}.{}", g.trim(), effective_doc_var, g.trim()))
            .collect();
        parts.push(format!("COLLECT {}", collect_parts.join(", ")));
    }

    // --- SORT ---
    if !ordering.is_empty() {
        let effective_doc_var = if graph_opts.is_some() { "v" } else { doc_var };
        let sort_str = aql_process_ordering(py, ordering, effective_doc_var)?;
        if !sort_str.is_empty() {
            parts.push(sort_str);
        }
    }

    // --- LIMIT ---
    if limit > 0 {
        if offset > 0 {
            parts.push(format!("LIMIT {}, {}", offset, limit));
        } else {
            parts.push(format!("LIMIT {}", limit));
        }
    }

    // --- RETURN ---
    if graph_opts.is_some() {
        // Graph: return vertex by default
        if !fields.is_empty() {
            let field_parts: Vec<String> = fields
                .iter()
                .map(|f| format!("{}: v.{}", f.trim(), f.trim()))
                .collect();
            parts.push(format!("RETURN {{ {} }}", field_parts.join(", ")));
        } else {
            parts.push("RETURN v".to_string());
        }
    } else if !grouping.is_empty() {
        // COLLECT: return the grouped fields
        if !fields.is_empty() {
            let return_str = aql_process_fields(py, fields, doc_var)?;
            parts.push(return_str);
        } else {
            let group_return: Vec<String> = grouping
                .iter()
                .map(|g| g.trim().to_string())
                .collect();
            parts.push(format!("RETURN {{ {} }}", group_return.join(", ")));
        }
    } else {
        let return_str = aql_process_fields(py, fields, doc_var)?;
        parts.push(return_str);
    }

    Ok(parts.join("\n    "))
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // --- parse_field_key ---

    #[test]
    fn test_parse_field_key_normal() {
        let (name, suffix) = parse_field_key("status");
        assert_eq!(name, "status");
        assert_eq!(suffix, "");
    }

    #[test]
    fn test_parse_field_key_negation() {
        let (name, suffix) = parse_field_key("status!");
        assert_eq!(name, "status");
        assert_eq!(suffix, "!");
    }

    // --- sql_to_aql_op ---

    #[test]
    fn test_sql_to_aql_operators() {
        assert_eq!(sql_to_aql_op("="), Some("=="));
        assert_eq!(sql_to_aql_op(">"), Some(">"));
        assert_eq!(sql_to_aql_op(">="), Some(">="));
        assert_eq!(sql_to_aql_op("<"), Some("<"));
        assert_eq!(sql_to_aql_op("<="), Some("<="));
        assert_eq!(sql_to_aql_op("<>"), Some("!="));
        assert_eq!(sql_to_aql_op("!="), Some("!="));
        assert_eq!(sql_to_aql_op("LIKE"), Some("LIKE"));
        assert_eq!(sql_to_aql_op("unknown"), None);
    }

    // --- format_aql_value ---

    #[test]
    fn test_format_string_value() {
        let val = AqlValue::Str("hello".to_string());
        assert_eq!(format_aql_value(&val, &None), "\"hello\"");
    }

    #[test]
    fn test_format_int_value() {
        let val = AqlValue::Int(42);
        assert_eq!(format_aql_value(&val, &None), "42");
    }

    #[test]
    fn test_format_bool_value() {
        let val = AqlValue::Bool(true);
        assert_eq!(format_aql_value(&val, &None), "true");
    }

    #[test]
    fn test_format_null_string() {
        let val = AqlValue::Str("NULL".to_string());
        assert_eq!(format_aql_value(&val, &None), "NULL");
    }

    #[test]
    fn test_format_none_value() {
        let val = AqlValue::None;
        assert_eq!(format_aql_value(&val, &None), "null");
    }

    // --- convert_value ---

    #[test]
    fn test_convert_value_to_int() {
        let val = AqlValue::Str("42".to_string());
        let ft = Some("integer".to_string());
        match convert_value(&val, &ft) {
            AqlValue::Int(i) => assert_eq!(i, 42),
            _ => panic!("Expected Int"),
        }
    }

    #[test]
    fn test_convert_value_to_float() {
        let val = AqlValue::Str("3.14".to_string());
        let ft = Some("float".to_string());
        match convert_value(&val, &ft) {
            AqlValue::Float(f) => assert!((f - 3.14).abs() < 0.001),
            _ => panic!("Expected Float"),
        }
    }

    #[test]
    fn test_convert_value_to_bool() {
        let val = AqlValue::Str("true".to_string());
        let ft = Some("boolean".to_string());
        match convert_value(&val, &ft) {
            AqlValue::Bool(b) => assert!(b),
            _ => panic!("Expected Bool"),
        }
    }

    #[test]
    fn test_convert_value_to_string() {
        let val = AqlValue::Int(42);
        let ft = Some("string".to_string());
        match convert_value(&val, &ft) {
            AqlValue::Str(s) => assert_eq!(s, "42"),
            _ => panic!("Expected Str"),
        }
    }

    // --- process_aql_entry ---

    #[test]
    fn test_process_equality_str() {
        let entry = AqlFilterEntry {
            field_name: "name".to_string(),
            suffix: String::new(),
            value: AqlValue::Str("admin".to_string()),
            field_type: None,
        };
        let result = process_aql_entry(&entry, "doc").unwrap();
        assert_eq!(render_condition(&result), "doc.name == \"admin\"");
    }

    #[test]
    fn test_process_equality_int() {
        let entry = AqlFilterEntry {
            field_name: "age".to_string(),
            suffix: String::new(),
            value: AqlValue::Int(30),
            field_type: None,
        };
        let result = process_aql_entry(&entry, "doc").unwrap();
        assert_eq!(render_condition(&result), "doc.age == 30");
    }

    #[test]
    fn test_process_null() {
        let entry = AqlFilterEntry {
            field_name: "email".to_string(),
            suffix: String::new(),
            value: AqlValue::Str("null".to_string()),
            field_type: None,
        };
        let result = process_aql_entry(&entry, "doc").unwrap();
        assert_eq!(render_condition(&result), "doc.email == null");
    }

    #[test]
    fn test_process_not_null() {
        let entry = AqlFilterEntry {
            field_name: "email".to_string(),
            suffix: String::new(),
            value: AqlValue::Str("!null".to_string()),
            field_type: None,
        };
        let result = process_aql_entry(&entry, "doc").unwrap();
        assert_eq!(render_condition(&result), "doc.email != null");
    }

    #[test]
    fn test_process_negation() {
        let entry = AqlFilterEntry {
            field_name: "status".to_string(),
            suffix: String::new(),
            value: AqlValue::Str("!active".to_string()),
            field_type: None,
        };
        let result = process_aql_entry(&entry, "doc").unwrap();
        assert_eq!(render_condition(&result), "doc.status != \"active\"");
    }

    #[test]
    fn test_process_in_list() {
        let entry = AqlFilterEntry {
            field_name: "status".to_string(),
            suffix: String::new(),
            value: AqlValue::List(vec![
                AqlValue::Str("active".to_string()),
                AqlValue::Str("pending".to_string()),
            ]),
            field_type: None,
        };
        let result = process_aql_entry(&entry, "doc").unwrap();
        assert_eq!(
            render_condition(&result),
            "doc.status IN [\"active\", \"pending\"]"
        );
    }

    #[test]
    fn test_process_not_in_list() {
        let entry = AqlFilterEntry {
            field_name: "status".to_string(),
            suffix: "!".to_string(),
            value: AqlValue::List(vec![AqlValue::Str("deleted".to_string())]),
            field_type: None,
        };
        let result = process_aql_entry(&entry, "doc").unwrap();
        assert_eq!(
            render_condition(&result),
            "doc.status NOT IN [\"deleted\"]"
        );
    }

    #[test]
    fn test_process_dict_operator() {
        let entry = AqlFilterEntry {
            field_name: "age".to_string(),
            suffix: String::new(),
            value: AqlValue::Dict {
                op: ">=".to_string(),
                val: Box::new(AqlValue::Int(18)),
            },
            field_type: None,
        };
        let result = process_aql_entry(&entry, "doc").unwrap();
        assert_eq!(render_condition(&result), "doc.age >= 18");
    }

    #[test]
    fn test_process_between() {
        let entry = AqlFilterEntry {
            field_name: "created_at".to_string(),
            suffix: String::new(),
            value: AqlValue::Str("BETWEEN 2024-01-01 AND 2024-12-31".to_string()),
            field_type: None,
        };
        let result = process_aql_entry(&entry, "doc").unwrap();
        let rendered = render_condition(&result);
        assert!(rendered.contains("doc.created_at >= "));
        assert!(rendered.contains("AND doc.created_at <= "));
    }

    #[test]
    fn test_process_bool() {
        let entry = AqlFilterEntry {
            field_name: "active".to_string(),
            suffix: String::new(),
            value: AqlValue::Bool(true),
            field_type: None,
        };
        let result = process_aql_entry(&entry, "doc").unwrap();
        assert_eq!(render_condition(&result), "doc.active == true");
    }

    #[test]
    fn test_process_none() {
        let entry = AqlFilterEntry {
            field_name: "deleted".to_string(),
            suffix: String::new(),
            value: AqlValue::None,
            field_type: None,
        };
        let result = process_aql_entry(&entry, "doc").unwrap();
        assert_eq!(render_condition(&result), "doc.deleted == null");
    }

    #[test]
    fn test_process_like_pattern() {
        let entry = AqlFilterEntry {
            field_name: "name".to_string(),
            suffix: String::new(),
            value: AqlValue::Str("%john%".to_string()),
            field_type: None,
        };
        let result = process_aql_entry(&entry, "doc").unwrap();
        assert_eq!(render_condition(&result), "LIKE(doc.name, \"%john%\")");
    }

    #[test]
    fn test_process_like_operator() {
        let entry = AqlFilterEntry {
            field_name: "name".to_string(),
            suffix: String::new(),
            value: AqlValue::Dict {
                op: "LIKE".to_string(),
                val: Box::new(AqlValue::Str("%admin%".to_string())),
            },
            field_type: None,
        };
        let result = process_aql_entry(&entry, "doc").unwrap();
        assert_eq!(render_condition(&result), "LIKE(doc.name, \"%admin%\")");
    }

    // --- render_condition ---

    #[test]
    fn test_render_in_list() {
        let cond = AqlCondition::InList(
            "doc.tags".to_string(),
            false,
            vec!["\"a\"".to_string(), "\"b\"".to_string()],
        );
        assert_eq!(render_condition(&cond), "doc.tags IN [\"a\", \"b\"]");
    }

    #[test]
    fn test_render_not_in_list() {
        let cond = AqlCondition::InList(
            "doc.tags".to_string(),
            true,
            vec!["\"x\"".to_string()],
        );
        assert_eq!(render_condition(&cond), "doc.tags NOT IN [\"x\"]");
    }

    // --- GraphOptions ---

    #[test]
    fn test_graph_options_defaults() {
        let opts = GraphOptions {
            direction: "OUTBOUND".to_string(),
            start_vertex: "users/123".to_string(),
            edge_collection: "follows".to_string(),
            min_depth: 1,
            max_depth: 3,
        };
        assert_eq!(opts.direction, "OUTBOUND");
        assert_eq!(opts.min_depth, 1);
        assert_eq!(opts.max_depth, 3);
    }

    // --- SearchOptions ---

    #[test]
    fn test_search_options_basic() {
        let opts = SearchOptions {
            view: "users_view".to_string(),
            analyzer: Some("text_en".to_string()),
            fields: vec![("name".to_string(), "John".to_string())],
            phrase: vec![],
        };
        assert_eq!(opts.view, "users_view");
        assert_eq!(opts.analyzer, Some("text_en".to_string()));
        assert_eq!(opts.fields.len(), 1);
    }

    // --- AqlOrderEntry ---

    #[test]
    fn test_order_entry_desc() {
        let oe = AqlOrderEntry {
            field: "created_at".to_string(),
            direction: "DESC".to_string(),
        };
        assert_eq!(oe.field, "created_at");
        assert_eq!(oe.direction, "DESC");
    }

    #[test]
    fn test_order_entry_asc() {
        let oe = AqlOrderEntry {
            field: "name".to_string(),
            direction: "ASC".to_string(),
        };
        assert_eq!(oe.field, "name");
        assert_eq!(oe.direction, "ASC");
    }
}
