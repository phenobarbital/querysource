use pyo3::prelude::*;

mod parser;
mod sql;

use sql::SQLParser;

/// A Python module implemented in Rust.
#[pymodule]
fn qs_parser(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SQLParser>()?;
    Ok(())
}
