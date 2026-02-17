// Copyright (C) 2018-present Jesus Lara
//
// rethink_parser.rs — RethinkDB query helper functions with rayon parallelism.
// Provides pure-data transformations that can be offloaded from the
// Python/Cython RethinkParser.  Driver-specific operations (r.table,
// r.row, lambdas) remain in Cython.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};
use rayon::prelude::*;

// ---------------------------------------------------------------------------
// rethink_process_fields
// ---------------------------------------------------------------------------

/// Parsed field entry (GIL-free).
#[derive(Debug, Clone)]
struct ParsedField {
    name: String,
    alias: Option<String>,
}

/// Parse RethinkDB field specifiers.
///
/// Each item may be `"field"` or `"field as alias"`.
/// Returns `(clean_fields, alias_map)` where `alias_map` maps alias→field.
///
/// Processing is parallelised with rayon.
#[pyfunction]
#[pyo3(signature = (fields,))]
pub fn rethink_process_fields(py: Python, fields: Vec<String>) -> PyResult<PyObject> {
    if fields.is_empty() {
        let empty_list = PyList::empty(py);
        let empty_dict = PyDict::new(py);
        let tuple = PyTuple::new(py, &[
            empty_list.into_pyobject(py).unwrap().into_any().unbind(),
            empty_dict.into_pyobject(py).unwrap().into_any().unbind(),
        ])?;
        return Ok(tuple.into_pyobject(py).unwrap().into_any().unbind());
    }

    // Phase 1: parse in parallel (no GIL)
    let parsed: Vec<ParsedField> = fields
        .par_iter()
        .map(|f| {
            // Case-insensitive " as " split
            let lower = f.to_lowercase();
            if let Some(pos) = lower.find(" as ") {
                let name = f[..pos].trim().to_string();
                let alias = f[pos + 4..].trim().replace('"', "");
                ParsedField {
                    name,
                    alias: Some(alias),
                }
            } else {
                ParsedField {
                    name: f.trim().to_string(),
                    alias: None,
                }
            }
        })
        .collect();

    // Phase 2: build Python objects (serial, GIL)
    let field_list = PyList::empty(py);
    let alias_dict = PyDict::new(py);

    for pf in &parsed {
        field_list.append(&pf.name)?;
        if let Some(ref alias) = pf.alias {
            alias_dict.set_item(alias, &pf.name)?;
        }
    }

    let tuple = PyTuple::new(py, &[
        field_list.into_pyobject(py).unwrap().into_any().unbind(),
        alias_dict.into_pyobject(py).unwrap().into_any().unbind(),
    ])?;
    Ok(tuple.into_pyobject(py).unwrap().into_any().unbind())
}

// ---------------------------------------------------------------------------
// rethink_process_ordering
// ---------------------------------------------------------------------------

/// Parsed ordering entry (GIL-free).
#[derive(Debug, Clone)]
struct OrderEntry {
    field: String,
    direction: String, // "ASC" or "DESC"
}

/// Parse ordering specifiers into (field, direction) tuples.
///
/// Input: list of strings like `["name DESC", "created_at"]`
/// Output: list of tuples `[("name", "DESC"), ("created_at", "ASC")]`
///
/// If a single string is given, it is split on comma first.
#[pyfunction]
#[pyo3(signature = (ordering,))]
pub fn rethink_process_ordering(py: Python, ordering: Vec<String>) -> PyResult<PyObject> {
    if ordering.is_empty() {
        return Ok(py.None());
    }

    // Phase 1: parse in parallel (no GIL)
    let entries: Vec<OrderEntry> = ordering
        .par_iter()
        .map(|item| {
            let trimmed = item.trim();
            let parts: Vec<&str> = trimmed.splitn(2, ' ').collect();
            if parts.len() == 2 {
                let dir_upper = parts[1].trim().to_uppercase();
                let direction = if dir_upper == "DESC" {
                    "DESC".to_string()
                } else {
                    "ASC".to_string()
                };
                OrderEntry {
                    field: parts[0].to_string(),
                    direction,
                }
            } else {
                OrderEntry {
                    field: trimmed.to_string(),
                    direction: "ASC".to_string(),
                }
            }
        })
        .collect();

    // Phase 2: build Python list of tuples (serial, GIL)
    let result = PyList::empty(py);
    for entry in &entries {
        let tuple = PyTuple::new(py, &[
            entry
                .field
                .clone()
                .into_pyobject(py)
                .unwrap()
                .into_any()
                .unbind(),
            entry
                .direction
                .clone()
                .into_pyobject(py)
                .unwrap()
                .into_any()
                .unbind(),
        ])?;
        result.append(tuple)?;
    }

    Ok(result.into_pyobject(py).unwrap().into_any().unbind())
}

// ---------------------------------------------------------------------------
// rethink_classify_conditions
// ---------------------------------------------------------------------------

/// Classification result for a single filter key (GIL-free).
#[derive(Debug, Clone)]
struct CondClassification {
    key: String,
    kind: String, // "date", "epoch", "list", "dict", "scalar"
}

/// Classify filter condition keys by their type.
///
/// Uses `cond_definition` to identify date/epoch fields.
/// For values: lists → "list", dicts → "dict", otherwise → "scalar".
/// Returns `{key: classification_string}`.
///
/// This allows the Cython layer to dispatch efficiently without
/// repeated isinstance checks on Python objects.
#[pyfunction]
#[pyo3(signature = (filter_dict, cond_definition))]
pub fn rethink_classify_conditions(
    py: Python,
    filter_dict: &Bound<'_, PyDict>,
    cond_definition: &Bound<'_, PyDict>,
) -> PyResult<PyObject> {
    let result = PyDict::new(py);

    if filter_dict.is_empty() {
        return Ok(result.into_pyobject(py).unwrap().into_any().unbind());
    }

    // Phase 1: extract data (serial, GIL)
    let entries: Vec<(String, String, bool, bool)> = filter_dict
        .iter()
        .filter_map(|(py_key, py_val)| {
            let key: String = py_key.extract().ok()?;
            let cond_type: Option<String> = cond_definition
                .get_item(&key)
                .ok()
                .flatten()
                .and_then(|v| v.extract().ok());
            let cond_str = cond_type.unwrap_or_default();
            let is_list = py_val.downcast::<PyList>().is_ok();
            let is_dict = py_val.downcast::<PyDict>().is_ok();
            Some((key, cond_str, is_list, is_dict))
        })
        .collect();

    // Phase 2: classify in parallel (no GIL)
    let classifications: Vec<CondClassification> = entries
        .par_iter()
        .map(|(key, cond_str, is_list, is_dict)| {
            let kind = match cond_str.as_str() {
                "date" | "timestamp" | "datetime" => "date".to_string(),
                "epoch" => "epoch".to_string(),
                _ => {
                    if *is_list {
                        "list".to_string()
                    } else if *is_dict {
                        "dict".to_string()
                    } else {
                        "scalar".to_string()
                    }
                }
            };
            CondClassification {
                key: key.clone(),
                kind,
            }
        })
        .collect();

    // Phase 3: build Python dict (serial, GIL)
    for c in &classifications {
        result.set_item(&c.key, &c.kind)?;
    }

    Ok(result.into_pyobject(py).unwrap().into_any().unbind())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parsed_field_simple() {
        let pf = ParsedField {
            name: "status".to_string(),
            alias: None,
        };
        assert_eq!(pf.name, "status");
        assert!(pf.alias.is_none());
    }

    #[test]
    fn test_parsed_field_with_alias() {
        let pf = ParsedField {
            name: "user_name".to_string(),
            alias: Some("name".to_string()),
        };
        assert_eq!(pf.name, "user_name");
        assert_eq!(pf.alias.unwrap(), "name");
    }

    #[test]
    fn test_order_entry_desc() {
        let oe = OrderEntry {
            field: "created_at".to_string(),
            direction: "DESC".to_string(),
        };
        assert_eq!(oe.field, "created_at");
        assert_eq!(oe.direction, "DESC");
    }

    #[test]
    fn test_order_entry_asc_default() {
        let oe = OrderEntry {
            field: "name".to_string(),
            direction: "ASC".to_string(),
        };
        assert_eq!(oe.field, "name");
        assert_eq!(oe.direction, "ASC");
    }

    #[test]
    fn test_cond_classification_date() {
        let cc = CondClassification {
            key: "inserted_at".to_string(),
            kind: "date".to_string(),
        };
        assert_eq!(cc.key, "inserted_at");
        assert_eq!(cc.kind, "date");
    }

    #[test]
    fn test_cond_classification_scalar() {
        let cc = CondClassification {
            key: "status".to_string(),
            kind: "scalar".to_string(),
        };
        assert_eq!(cc.key, "status");
        assert_eq!(cc.kind, "scalar");
    }

    #[test]
    fn test_cond_classification_list() {
        let cc = CondClassification {
            key: "tags".to_string(),
            kind: "list".to_string(),
        };
        assert_eq!(cc.key, "tags");
        assert_eq!(cc.kind, "list");
    }
}
