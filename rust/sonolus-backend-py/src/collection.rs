//! `PyO3` bindings for the collection core (PORT.md task T5.2).
//!
//! Division of labor (ARCHITECTURE.md section 7): Python keeps orchestration —
//! the `categories` item dicts that the build pipeline mutates in place, user
//! level-converter callbacks, and URL fetching (every asset reaches Rust as
//! bytes). Rust persistently holds the SHA1 content-addressed blob repository
//! and performs the heavy operations: scp/zip parsing, SHA1 hashing, resource
//! gzip, level/engine linking, `CPython`-exact JSON serialization, and the
//! site-tree write with skip-if-hash-exists.
//!
//! Categories cross the FFI as JSON strings:
//!
//! - loads (`load_from_scp` / `load_from_source`) run on a transient core
//!   collection whose blobs are merged into the persistent repository and
//!   whose categories are returned to Python (which merges them into its
//!   plain dicts, preserving the legacy in-place-mutation API);
//! - `write` receives a categories snapshot, links it in Rust, writes the
//!   site tree, and returns the linked snapshot so Python can adopt it (the
//!   legacy `link` mutates the collection in place; adopting the linked state
//!   keeps the dev-server rebuild state evolution identical).
//!
//! JSON returned to Python is produced by the iterative
//! [`pyjson`](sonolus_backend_core::collection::pyjson) serializer
//! (`json.loads`-compatible by construction).

use std::path::Path;

use indexmap::IndexMap;
use pyo3::exceptions::{PyKeyError, PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use serde_json::Value;
use sonolus_backend_core::collection::{
    Collection as CoreCollection, CollectionError, Srl, WriteStats, pyjson, sha1_hex,
};

/// Maps a core collection error onto the closest Python exception: the
/// not-found errors mirror the legacy `KeyError` messages exactly; IO errors
/// become `OSError`; everything else (bad zip data, malformed item shapes)
/// becomes `ValueError`. Error *values* on garbage inputs are a documented
/// T5.1 divergence from the exact legacy exception types.
fn collection_error_to_py(e: &CollectionError) -> PyErr {
    match e {
        CollectionError::ItemNotFound { .. } | CollectionError::NoItems { .. } => {
            PyKeyError::new_err(e.to_string())
        }
        CollectionError::Io(_) => PyOSError::new_err(e.to_string()),
        CollectionError::Zip(_) | CollectionError::InvalidItem(_) => {
            PyValueError::new_err(e.to_string())
        }
    }
}

fn write_stats_to_dict(py: Python<'_>, stats: WriteStats) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("json_files_written", stats.json_files_written)?;
    dict.set_item("repository_files_written", stats.repository_files_written)?;
    dict.set_item("repository_files_skipped", stats.repository_files_skipped)?;
    Ok(dict.unbind())
}

/// The Rust half of the T5.2 `Collection`: the persistent blob repository plus
/// the collection operations. The Python wrapper
/// (`sonolus.build.rust_collection.RustCollection`) owns `name` and the
/// `categories` dicts and delegates here.
#[pyclass]
pub struct Collection {
    repository: IndexMap<String, Vec<u8>>,
}

#[pymethods]
#[allow(clippy::needless_pass_by_value)] // PyO3 argument convention
impl Collection {
    #[new]
    fn new() -> Self {
        Self {
            repository: IndexMap::new(),
        }
    }

    /// Stores `data` under its SHA1 hex digest and returns `(hash, url)`
    /// (the legacy `add_asset`, minus the Python-side asset loading).
    fn add_asset(&mut self, data: Vec<u8>) -> (String, String) {
        let hash = sha1_hex(&data);
        self.repository.insert(hash.clone(), data);
        let srl = Srl::for_hash(hash);
        (srl.hash, srl.url)
    }

    /// Loads an `.scp` archive: repository blobs merge into the persistent
    /// repository (legacy dict-update semantics: overwritten keys keep their
    /// position, new keys append) and the loaded categories are returned as a
    /// `{category: {name: details}}` JSON string for Python to merge.
    ///
    /// Raises `ValueError` when the bytes are not a readable zip archive
    /// (the legacy raises `zipfile.BadZipFile`; documented divergence).
    fn load_from_scp(&mut self, zip_data: &[u8]) -> PyResult<String> {
        let mut transient = CoreCollection::new();
        transient
            .load_from_scp(zip_data)
            .map_err(|e| collection_error_to_py(&e))?;
        let categories = pyjson::dumps(&transient.categories_value());
        let (_, blobs) = transient.into_parts();
        self.repository.extend(blobs);
        Ok(categories)
    }

    /// Loads a source resource tree (resources gzipped/hashed into the
    /// persistent repository) and returns the categories JSON plus the
    /// warning messages the legacy implementation would emit (Python re-emits
    /// them through `warnings.warn`/`print`).
    fn load_from_source(&mut self, path: &str) -> PyResult<(String, Vec<String>)> {
        let mut transient = CoreCollection::new();
        let warnings = transient
            .load_from_source(Path::new(path))
            .map_err(|e| collection_error_to_py(&e))?;
        let categories = pyjson::dumps(&transient.categories_value());
        let (_, blobs) = transient.into_parts();
        self.repository.extend(blobs);
        Ok((categories, warnings))
    }

    /// Links and writes the site tree under `path` from a categories JSON
    /// snapshot, using the persistent repository for blobs. Returns a stats
    /// dict (`json_files_written` / `repository_files_written` /
    /// `repository_files_skipped`) and the post-link categories JSON, which
    /// Python adopts to mirror the legacy in-place `link`.
    ///
    /// Raises `KeyError` when a level references a missing engine (legacy
    /// message preserved) and `ValueError`/`OSError` on malformed shapes and
    /// filesystem errors.
    fn write(
        &mut self,
        py: Python<'_>,
        path: &str,
        name: &str,
        categories_json: &str,
    ) -> PyResult<(Py<PyDict>, String)> {
        let value: Value = serde_json::from_str(categories_json)
            .map_err(|e| PyValueError::new_err(format!("invalid categories JSON: {e}")))?;
        let mut transient = CoreCollection::new();
        name.clone_into(&mut transient.name);
        transient
            .set_categories_from_value(&value)
            .map_err(|e| collection_error_to_py(&e))?;
        // Lend the persistent repository to the transient collection for the
        // duration of the write (no blob copies; swapped back even on error).
        std::mem::swap(&mut self.repository, transient.repository_mut());
        let result = transient.write(Path::new(path));
        std::mem::swap(&mut self.repository, transient.repository_mut());
        let stats = result.map_err(|e| collection_error_to_py(&e))?;
        let linked = pyjson::dumps(&transient.categories_value());
        Ok((write_stats_to_dict(py, stats)?, linked))
    }

    /// Merges another collection's repository into this one (legacy
    /// dict-update semantics). The Python wrapper merges categories itself.
    fn update_from(&mut self, other: PyRef<'_, Collection>) {
        for (hash, data) in &other.repository {
            self.repository.insert(hash.clone(), data.clone());
        }
    }

    /// The blob stored under `key`, or `None`.
    fn repository_get(&self, key: &str) -> Option<Vec<u8>> {
        self.repository.get(key).cloned()
    }

    /// Stores `data` under `key` (an overwritten key keeps its position).
    fn repository_set(&mut self, key: String, data: Vec<u8>) {
        self.repository.insert(key, data);
    }

    /// Removes `key`, preserving the order of the remaining entries (Python
    /// `del` semantics). Returns whether the key existed.
    fn repository_remove(&mut self, key: &str) -> bool {
        self.repository.shift_remove(key).is_some()
    }

    /// Whether a blob is stored under `key`.
    fn repository_contains(&self, key: &str) -> bool {
        self.repository.contains_key(key)
    }

    /// The number of stored blobs.
    fn repository_len(&self) -> usize {
        self.repository.len()
    }

    /// All repository keys, in insertion order.
    fn repository_keys(&self) -> Vec<String> {
        self.repository.keys().cloned().collect()
    }
}

impl std::fmt::Debug for Collection {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Collection")
            .field("repository_len", &self.repository.len())
            .finish()
    }
}
