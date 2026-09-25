//! Binding PyO3 (feature `python`, compilado con `maturin develop --features python`).
//!
//! ```python
//! import json
//! from querysource.qsurl import _qsurl as qsurl  # installed as querysource.qsurl._qsurl
//! ir = json.loads(qsurl.parse("/queries/hisense_stores{id,name}?state='CA':top(10)"))
//! # ir["filter"] -> [{"column": "state", "expression": "==", "value": "CA"}] dentro de {"and": [...]}
//! try:
//!     qsurl.parse("stores:order(name)")
//! except ValueError as e:
//!     print(e)  # JSON con offset, message, expected y pointer: se lo devuelves al modelo tal cual
//! ```

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Parsea una URL del dialecto Querysource y devuelve el IR como JSON (str).
///
/// Lanza `ValueError` cuyo mensaje es un JSON con `kind`, `offset`,
/// `message`, `expected` y `pointer`, pensado para devolverlo al LLM.
#[pyfunction]
fn parse(src: &str) -> PyResult<String> {
    crate::parse_to_json(src).map_err(|e| PyValueError::new_err(e.to_json()))
}

/// Sólo valida; devuelve la lista de capacidades (`requires`) que la consulta
/// exige, para negociar pushdown con el driver antes de ejecutar nada.
#[pyfunction]
fn requires(src: &str) -> PyResult<Vec<String>> {
    let ir = crate::parse_to_ir(src).map_err(|e| PyValueError::new_err(e.to_json()))?;
    Ok(ir["requires"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default())
}

#[pymodule]
fn _qsurl(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse, m)?)?;
    m.add_function(wrap_pyfunction!(requires, m)?)?;
    Ok(())
}
