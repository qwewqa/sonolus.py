//! Thin `PyO3` bindings exposing `sonolus-backend-core` to Python as the
//! `sonolus_backend` extension module.

use pyo3::prelude::*;

/// Returns the version of the Rust backend.
#[pyfunction]
fn backend_version() -> &'static str {
    sonolus_backend_core::version()
}

/// The `sonolus_backend` Python extension module.
#[pymodule(gil_used = false)]
fn sonolus_backend(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(backend_version, m)?)?;
    Ok(())
}
