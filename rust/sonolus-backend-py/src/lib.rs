//! Thin `PyO3` bindings exposing `sonolus-backend-core` to Python as the
//! `sonolus_backend` extension module.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use sonolus_backend_core::cfg::{canonical_dump, cfg_to_text};
use sonolus_backend_core::decode::decode_cfg;

/// Returns the version of the Rust backend.
#[pyfunction]
fn backend_version() -> &'static str {
    sonolus_backend_core::version()
}

/// Decodes an encoded CFG (see `rust/ENCODING.md`) and returns its canonical
/// structural dump, byte-identical to the Python side's
/// `sonolus.backend.encode.cfg_canonical_dump` for the same CFG.
///
/// Test handle for round-trip validation. Raises `ValueError` on malformed input.
#[pyfunction]
fn decode_cfg_canonical_dump(data: &[u8]) -> PyResult<String> {
    let cfg = decode_cfg(data).map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(canonical_dump(&cfg))
}

/// Decodes an encoded CFG and returns a human-readable debug dump (Rust-native
/// formatting; decision D7 — not a compatibility surface).
///
/// Raises `ValueError` on malformed input.
#[pyfunction]
fn decode_cfg_debug_dump(data: &[u8]) -> PyResult<String> {
    let cfg = decode_cfg(data).map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(cfg_to_text(&cfg))
}

/// The `sonolus_backend` Python extension module.
#[pymodule(gil_used = false)]
fn sonolus_backend(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(backend_version, m)?)?;
    m.add_function(wrap_pyfunction!(decode_cfg_canonical_dump, m)?)?;
    m.add_function(wrap_pyfunction!(decode_cfg_debug_dump, m)?)?;
    Ok(())
}
