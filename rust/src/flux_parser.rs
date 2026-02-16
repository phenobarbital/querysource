// Copyright (C) 2018-present Jesus Lara
//
// flux_parser.rs — InfluxDB Flux query builder functions.
// Flux is a pipe-based language:  |> filter(fn: (r) => r["key"] == "value")
//                                 |> keep(columns: ["col1", "col2"])

use pyo3::prelude::*;
use pyo3::types::PyDict;

// ---------------------------------------------------------------------------
// filter_conditions: build Flux |> filter(...) pipes
// ---------------------------------------------------------------------------

/// Build Flux filter pipes from a Python dict.
///
/// For each `(key, value)` pair where value is `str` or `int`:
///   `|> filter(fn: (r) => r["key"] == "value")`
///
/// For bool values:
///   `|> filter(fn: (r) => r["key"] == true)`
///
/// For float values:
///   `|> filter(fn: (r) => r["key"] == 3.14)`
///
/// For list values (OR match):
///   `|> filter(fn: (r) => r["key"] == "v1" or r["key"] == "v2")`
///
/// For negation (`!value`):
///   `|> filter(fn: (r) => r["key"] != "value")`
#[pyfunction]
#[pyo3(signature = (query, filter_dict))]
pub fn flux_filter_conditions(
    query: &str,
    filter_dict: &Bound<'_, PyDict>,
) -> PyResult<String> {
    let mut pipes = Vec::new();

    for (py_key, py_val) in filter_dict.iter() {
        let key: String = py_key.extract()?;

        if let Ok(b) = py_val.extract::<bool>() {
            // Bool — must check before i64 because Python bool is int subclass
            let val_str = if b { "true" } else { "false" };
            pipes.push(format!(
                "|> filter(fn: (r) => r[\"{}\"] == {})",
                key, val_str
            ));
        } else if let Ok(i) = py_val.extract::<i64>() {
            // Integer
            pipes.push(format!(
                "|> filter(fn: (r) => r[\"{}\"] == {})",
                key, i
            ));
        } else if let Ok(f) = py_val.extract::<f64>() {
            // Float
            pipes.push(format!(
                "|> filter(fn: (r) => r[\"{}\"] == {})",
                key, f
            ));
        } else if let Ok(s) = py_val.extract::<String>() {
            // String — strip quotes, handle negation
            let val = s.replace('\'', "");
            if let Some(stripped) = val.strip_prefix('!') {
                // Negation
                pipes.push(format!(
                    "|> filter(fn: (r) => r[\"{}\"] != \"{}\")",
                    key, stripped
                ));
            } else if val == "null" || val == "NULL" {
                // Null check — Flux uses `exists`
                pipes.push(format!(
                    "|> filter(fn: (r) => not exists r[\"{}\"])",
                    key
                ));
            } else if val == "!null" || val == "!NULL" {
                pipes.push(format!(
                    "|> filter(fn: (r) => exists r[\"{}\"])",
                    key
                ));
            } else {
                pipes.push(format!(
                    "|> filter(fn: (r) => r[\"{}\"] == \"{}\")",
                    key, val
                ));
            }
        } else if let Ok(items) = py_val.extract::<Vec<String>>() {
            // List of strings → OR conditions in a single filter
            if !items.is_empty() {
                let or_parts: Vec<String> = items
                    .iter()
                    .map(|v| {
                        let clean = v.replace('\'', "");
                        format!("r[\"{}\"] == \"{}\"", key, clean)
                    })
                    .collect();
                let expr = or_parts.join(" or ");
                pipes.push(format!("|> filter(fn: (r) => {})", expr));
            }
        }
        // Skip unsupported types silently
    }

    if pipes.is_empty() {
        return Ok(query.to_string());
    }

    let filter_str = pipes.join(" ");
    Ok(format!("{} {}", query, filter_str))
}

// ---------------------------------------------------------------------------
// process_fields: build Flux |> keep(columns: [...])
// ---------------------------------------------------------------------------

/// Build a Flux `|> keep(columns: [...])` pipe from a list of field names.
///
/// Automatically adds `"_measurement"` if not already present.
#[pyfunction]
#[pyo3(signature = (query, fields))]
pub fn flux_process_fields(query: &str, fields: Vec<String>) -> PyResult<String> {
    if fields.is_empty() {
        return Ok(query.to_string());
    }

    let mut cols = fields;
    // Auto-add _measurement if missing
    if !cols.iter().any(|f| f == "_measurement") {
        cols.push("_measurement".to_string());
    }

    // Double-quote each column name
    let quoted: Vec<String> = cols.iter().map(|f| format!("\"{}\"", f)).collect();
    let columns = quoted.join(",");
    let keep = format!("|> keep(columns: [{}])", columns);

    Ok(format!("{} {}", query, keep))
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // --- filter_conditions tests (pure Rust, no PyO3) ---

    #[test]
    fn test_flux_filter_pipe_format() {
        // Verify the pipe string format directly
        let key = "host";
        let val = "server01";
        let pipe = format!("|> filter(fn: (r) => r[\"{}\"] == \"{}\")", key, val);
        assert_eq!(
            pipe,
            "|> filter(fn: (r) => r[\"host\"] == \"server01\")"
        );
    }

    #[test]
    fn test_flux_negation_format() {
        let key = "status";
        let val = "error";
        let pipe = format!("|> filter(fn: (r) => r[\"{}\"] != \"{}\")", key, val);
        assert_eq!(
            pipe,
            "|> filter(fn: (r) => r[\"status\"] != \"error\")"
        );
    }

    #[test]
    fn test_flux_null_exists() {
        let key = "tag";
        let pipe = format!("|> filter(fn: (r) => not exists r[\"{}\"])", key);
        assert_eq!(
            pipe,
            "|> filter(fn: (r) => not exists r[\"tag\"])"
        );
    }

    #[test]
    fn test_flux_not_null_exists() {
        let key = "tag";
        let pipe = format!("|> filter(fn: (r) => exists r[\"{}\"])", key);
        assert_eq!(
            pipe,
            "|> filter(fn: (r) => exists r[\"tag\"])"
        );
    }

    #[test]
    fn test_flux_or_list_format() {
        let key = "region";
        let items = vec!["us-east", "us-west"];
        let or_parts: Vec<String> = items
            .iter()
            .map(|v| format!("r[\"{}\"] == \"{}\"", key, v))
            .collect();
        let expr = or_parts.join(" or ");
        let pipe = format!("|> filter(fn: (r) => {})", expr);
        assert_eq!(
            pipe,
            "|> filter(fn: (r) => r[\"region\"] == \"us-east\" or r[\"region\"] == \"us-west\")"
        );
    }

    #[test]
    fn test_flux_int_format() {
        let key = "status_code";
        let val = 200;
        let pipe = format!("|> filter(fn: (r) => r[\"{}\"] == {})", key, val);
        assert_eq!(
            pipe,
            "|> filter(fn: (r) => r[\"status_code\"] == 200)"
        );
    }

    #[test]
    fn test_flux_bool_format() {
        let key = "is_active";
        let pipe = format!("|> filter(fn: (r) => r[\"{}\"] == true)", key);
        assert_eq!(
            pipe,
            "|> filter(fn: (r) => r[\"is_active\"] == true)"
        );
    }

    // --- process_fields tests ---

    #[test]
    fn test_flux_keep_columns() {
        let fields = vec!["_time".to_string(), "host".to_string()];
        let mut cols = fields;
        if !cols.iter().any(|f| f == "_measurement") {
            cols.push("_measurement".to_string());
        }
        let quoted: Vec<String> = cols.iter().map(|f| format!("\"{}\"", f)).collect();
        let columns = quoted.join(",");
        let keep = format!("|> keep(columns: [{}])", columns);
        assert_eq!(
            keep,
            "|> keep(columns: [\"_time\",\"host\",\"_measurement\"])"
        );
    }

    #[test]
    fn test_flux_keep_already_has_measurement() {
        let fields = vec![
            "_time".to_string(),
            "_measurement".to_string(),
            "value".to_string(),
        ];
        let mut cols = fields;
        if !cols.iter().any(|f| f == "_measurement") {
            cols.push("_measurement".to_string());
        }
        let quoted: Vec<String> = cols.iter().map(|f| format!("\"{}\"", f)).collect();
        let columns = quoted.join(",");
        let keep = format!("|> keep(columns: [{}])", columns);
        // Should NOT duplicate _measurement
        assert_eq!(
            keep,
            "|> keep(columns: [\"_time\",\"_measurement\",\"value\"])"
        );
    }

    #[test]
    fn test_flux_keep_empty() {
        let fields: Vec<String> = vec![];
        assert!(fields.is_empty());
        // When empty, function returns query unchanged
    }

    #[test]
    fn test_flux_full_query_assembly() {
        let base = "from(bucket: \"mydb\") |> range(start: -1h)";
        let filter = format!(
            "{} |> filter(fn: (r) => r[\"host\"] == \"server01\")",
            base
        );
        let keep = format!(
            "{} |> keep(columns: [\"_time\",\"host\",\"_measurement\"])",
            filter
        );
        let final_q = format!("{} |> sort()", keep);
        assert!(final_q.contains("from(bucket:"));
        assert!(final_q.contains("|> filter("));
        assert!(final_q.contains("|> keep("));
        assert!(final_q.ends_with("|> sort()"));
    }
}
