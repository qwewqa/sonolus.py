//! Core (pure-Rust) compiler backend for sonolus.py.
//!
//! This crate contains no Python bindings; it is consumed by `sonolus-backend-py`,
//! which exposes it to Python as the `sonolus_backend` extension module.

pub mod alloc;
pub mod analysis;
pub mod build;
pub mod cfg;
pub(crate) mod coalesce;
pub mod collection;
pub mod decode;
pub mod diff;
pub mod effects;
pub mod emit;
pub mod flatten;
pub mod interpret;
pub mod lower;
pub mod mir;
pub mod nodes;
pub mod ops;
pub mod output;
pub mod passes;
pub mod pipeline;
pub mod rewrite;
pub mod ssa;
pub mod tile;

/// Returns the version of the backend, taken from the crate metadata.
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_is_a_dotted_version_string() {
        let v = version();
        assert!(!v.is_empty());
        let parts: Vec<&str> = v.split('.').collect();
        assert!(parts.len() >= 2, "expected at least major.minor, got {v:?}");
        for part in parts {
            assert!(
                part.chars().all(|c| c.is_ascii_digit()),
                "non-numeric version component {part:?} in {v:?}"
            );
        }
    }
}
