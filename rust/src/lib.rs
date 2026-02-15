// Copyright (C) 2018-present Jesus Lara
//
// lib.rs — PyO3 module registration for qs_parsers
// Exposes Rust string-processing functions to Python for QuerySource parsers.

use pyo3::prelude::*;

mod filter_common;
mod mssql_parser;
mod parseqs;
mod pgsql_parser;
mod safe_dict;
mod soql_parser;
mod sql_parser;
mod validators;

/// qs_parsers — Rust-powered string processing for QuerySource.
///
/// This module provides high-performance replacements for Cython functions
/// used in QuerySource's SQL parser pipeline:
///
/// - Validators: field_components, quote_string, is_valid, strtobool, etc.
/// - ParseQS: is_parseable, parse_list, parse_tuple
/// - SafeDict: safe_format_map (placeholder replacement)
/// - SQL: filter_conditions, group_by, order_by, limiting, process_fields, build_sql
#[pymodule]
fn qs_parsers(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // -- Validators --
    m.add_function(wrap_pyfunction!(validators::strtobool, m)?)?;
    m.add_function(wrap_pyfunction!(validators::is_integer, m)?)?;
    m.add_function(wrap_pyfunction!(validators::is_float, m)?)?;
    m.add_function(wrap_pyfunction!(validators::is_boolean, m)?)?;
    m.add_function(wrap_pyfunction!(validators::is_udf, m)?)?;
    m.add_function(wrap_pyfunction!(validators::is_pgconstant, m)?)?;
    m.add_function(wrap_pyfunction!(validators::is_pg_function, m)?)?;
    m.add_function(wrap_pyfunction!(validators::field_components, m)?)?;
    m.add_function(wrap_pyfunction!(validators::escape_string, m)?)?;
    m.add_function(wrap_pyfunction!(validators::quote_string, m)?)?;
    m.add_function(wrap_pyfunction!(validators::to_string, m)?)?;
    m.add_function(wrap_pyfunction!(validators::is_valid, m)?)?;

    // -- ParseQS --
    m.add_function(wrap_pyfunction!(parseqs::is_parseable, m)?)?;
    m.add_function(wrap_pyfunction!(parseqs::parse_list, m)?)?;
    m.add_function(wrap_pyfunction!(parseqs::parse_tuple, m)?)?;

    // -- SafeDict --
    m.add_function(wrap_pyfunction!(safe_dict::safe_format_map, m)?)?;

    // -- SQL Parser --
    m.add_function(wrap_pyfunction!(sql_parser::filter_conditions, m)?)?;
    m.add_function(wrap_pyfunction!(sql_parser::group_by, m)?)?;
    m.add_function(wrap_pyfunction!(sql_parser::order_by, m)?)?;
    m.add_function(wrap_pyfunction!(sql_parser::limiting, m)?)?;
    m.add_function(wrap_pyfunction!(sql_parser::process_fields, m)?)?;
    m.add_function(wrap_pyfunction!(sql_parser::build_sql, m)?)?;

    // -- PgSQL Parser --
    m.add_function(wrap_pyfunction!(pgsql_parser::pgsql_filter_conditions, m)?)?;

    // -- MSSQL Parser --
    m.add_function(wrap_pyfunction!(mssql_parser::mssql_filter_conditions, m)?)?;

    // -- SOQL Parser --
    m.add_function(wrap_pyfunction!(soql_parser::soql_filter_conditions, m)?)?;

    // -- Additional validators --
    m.add_function(wrap_pyfunction!(validators::is_camel_case, m)?)?;

    Ok(())
}
