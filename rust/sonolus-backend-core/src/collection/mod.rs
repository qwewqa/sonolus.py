//! Collection assembly and site-tree packaging (PORT.md task T5.1).
//!
//! Pure-Rust port of the frozen `sonolus/build/collection.py`: loading `.scp`
//! archives (zip) and source resource trees into an in-memory collection
//! (items by category + a SHA1 content-addressed repository), item-text
//! localization, level/engine linking, and writing the site directory tree.
//!
//! Item JSON is kept opaque-but-parseable as [`serde_json::Value`] with the
//! `preserve_order` feature, because key order reaches the output bytes.
//! Site-tree JSON files are written in `CPython`'s `json.dumps` default format
//! (see [`pyjson`]) so the output is byte-compatible with the legacy backend.
//!
//! Scope notes (per ARCHITECTURE.md section 7): URL fetching stays in Python
//! -- every asset enters this module as bytes (or a local path during
//! [`Collection::load_from_source`]). The `PyO3` `Collection` wrapper arrives
//! in task T5.2; this module exposes the equivalent core operations plus raw
//! category/repository accessors for it to build on.
//!
//! Behavioral fidelity to the frozen Python reference, including quirks:
//!
//! - repository keys loaded from an scp are taken from the file name and are
//!   *not* verified to be the SHA1 of the contents;
//! - `link` resolves a level's `"engine"` only when it is a string, and the
//!   four `use*` references only when present and matching the reference's
//!   shapes; unresolved string references are left in place silently;
//! - reserved file names (`info`, `list`, case-insensitive) are skipped on
//!   scp load; categories mentioned by an scp exist even if every item in
//!   them fails to parse; invalid item JSON is skipped silently (scp) or with
//!   a warning (source tree);
//! - item names from scp entries use `CPython` 3.14 `PurePath.stem` semantics
//!   (`os.path.splitext`: leading dots are not suffix separators).
//!
//! Deliberate divergences (all on error/garbage paths, documented for T5.2):
//!
//! - Python raises `KeyError`/`TypeError` on malformed item shapes; this
//!   module returns [`CollectionError`] values with equivalent messages where
//!   the message is part of the observable behavior (`get_item`,
//!   `get_default_item`).
//! - Python crashes (`UnicodeDecodeError`) on non-UTF-8 item JSON; this
//!   module treats it as invalid JSON (skip/warn).
//! - `load_from_source` visits directory entries sorted by file name instead
//!   of OS enumeration order, making the resulting item order deterministic
//!   (invariant 5); Python inherits the platform's `os.scandir` order.

pub mod pyjson;

use std::fmt;
use std::fs;
use std::io::{Cursor, Read as _, Write as _};
use std::path::{Path, PathBuf};

use indexmap::IndexMap;
use serde_json::{Map, Value, json};
use sha1::{Digest as _, Sha1};

/// The URL prefix every repository asset is served under.
pub const BASE_PATH: &str = "/sonolus/";

/// Keys whose values are localized text (a string or a language-to-text map).
pub const LOCALIZED_KEYS: [&str; 5] = ["title", "subtitle", "author", "description", "artists"];

/// File names (case-insensitive, after stripping directories) that are
/// skipped when loading an scp because they are generated on write.
const RESERVED_FILENAMES: [&str; 2] = ["info", "list"];

/// An item category. The set and spellings mirror `collection.py`'s
/// `CATEGORY_NAMES`; anything else in an scp or source tree is ignored.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Category {
    Posts,
    Playlists,
    Levels,
    Replays,
    Skins,
    Backgrounds,
    Effects,
    Particles,
    Engines,
}

impl Category {
    /// All categories, in `CATEGORY_NAMES` declaration order.
    pub const ALL: [Category; 9] = [
        Category::Posts,
        Category::Playlists,
        Category::Levels,
        Category::Replays,
        Category::Skins,
        Category::Backgrounds,
        Category::Effects,
        Category::Particles,
        Category::Engines,
    ];

    /// Parses a directory name into a category (case-sensitive).
    pub fn from_name(name: &str) -> Option<Category> {
        Category::ALL.iter().copied().find(|c| c.as_str() == name)
    }

    /// The plural directory name (`"levels"`, ...).
    pub fn as_str(self) -> &'static str {
        match self {
            Category::Posts => "posts",
            Category::Playlists => "playlists",
            Category::Levels => "levels",
            Category::Replays => "replays",
            Category::Skins => "skins",
            Category::Backgrounds => "backgrounds",
            Category::Effects => "effects",
            Category::Particles => "particles",
            Category::Engines => "engines",
        }
    }

    /// The singular item-type name used in info/list JSON.
    pub fn singular(self) -> &'static str {
        match self {
            Category::Posts => "post",
            Category::Playlists => "playlist",
            Category::Levels => "level",
            Category::Replays => "replay",
            Category::Skins => "skin",
            Category::Backgrounds => "background",
            Category::Effects => "effect",
            Category::Particles => "particle",
            Category::Engines => "engine",
        }
    }

    /// Sort key for the main info buttons (`CATEGORY_SORT_ORDER`).
    pub fn sort_order(self) -> u32 {
        match self {
            Category::Levels => 0,
            Category::Engines => 1,
            Category::Skins => 2,
            Category::Effects => 3,
            Category::Particles => 4,
            Category::Backgrounds => 5,
            Category::Posts => 6,
            Category::Playlists => 7,
            Category::Replays => 8,
        }
    }
}

impl fmt::Display for Category {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// A Sonolus resource locator: content hash plus repository URL.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Srl {
    pub hash: String,
    pub url: String,
}

impl Srl {
    /// Builds the SRL for a repository hash.
    pub fn for_hash(hash: impl Into<String>) -> Srl {
        let hash = hash.into();
        let url = format!("{BASE_PATH}repository/{hash}");
        Srl { hash, url }
    }

    /// The JSON object form, with the legacy `hash`, `url` key order.
    pub fn to_value(&self) -> Value {
        json!({"hash": self.hash, "url": self.url})
    }
}

/// An error from a collection operation.
///
/// Where Python raises `KeyError` with a meaningful message (`get_item`,
/// `get_default_item`, link resolution), the [`fmt::Display`] output matches
/// that message exactly.
#[derive(Debug)]
pub enum CollectionError {
    /// An underlying filesystem error.
    Io(std::io::Error),
    /// The scp bytes are not a readable zip archive.
    Zip(String),
    /// A referenced item does not exist (Python `KeyError` message).
    ItemNotFound { category: Category, name: String },
    /// A category has no items (Python `KeyError` message).
    NoItems { category: Category },
    /// An item or item-details value has an invalid shape (paths where the
    /// Python reference would raise `KeyError`/`TypeError` on the data).
    InvalidItem(String),
}

impl fmt::Display for CollectionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CollectionError::Io(e) => write!(f, "{e}"),
            CollectionError::Zip(message) => write!(f, "invalid scp archive: {message}"),
            CollectionError::ItemNotFound { category, name } => {
                write!(f, "Item '{name}' not found in category '{category}'")
            }
            CollectionError::NoItems { category } => {
                write!(f, "No items found in category '{category}'")
            }
            CollectionError::InvalidItem(message) => write!(f, "{message}"),
        }
    }
}

impl std::error::Error for CollectionError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            CollectionError::Io(e) => Some(e),
            _ => None,
        }
    }
}

impl From<std::io::Error> for CollectionError {
    fn from(e: std::io::Error) -> Self {
        CollectionError::Io(e)
    }
}

/// Counters for one [`Collection::write`] call. The repository counters make
/// the skip-if-hash-exists behavior observable to callers and tests.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct WriteStats {
    /// JSON files written (main info + per-category info/list/items).
    pub json_files_written: usize,
    /// Repository blobs written because no file with that hash existed.
    pub repository_files_written: usize,
    /// Repository blobs skipped because the target file already existed.
    pub repository_files_skipped: usize,
}

/// All item details by category then item name (insertion-ordered).
pub type Categories = IndexMap<Category, IndexMap<String, Value>>;

/// The SHA1 content-addressed blob repository (insertion-ordered).
pub type Repository = IndexMap<String, Vec<u8>>;

/// An in-memory Sonolus collection: items by category plus a SHA1
/// content-addressed blob repository. Port of `collection.py::Collection`.
///
/// All maps are insertion-ordered; category order, item order, and item JSON
/// key order all reach the written site tree.
#[derive(Debug, Clone)]
pub struct Collection {
    /// The collection title written to the main info file.
    pub name: String,
    categories: Categories,
    repository: Repository,
}

impl Default for Collection {
    fn default() -> Self {
        Collection::new()
    }
}

impl Collection {
    /// Creates an empty collection named `"Unnamed"`.
    pub fn new() -> Collection {
        Collection {
            name: "Unnamed".to_owned(),
            categories: IndexMap::new(),
            repository: IndexMap::new(),
        }
    }

    /// Returns the item (the `"item"` field of the stored details) for
    /// `name` in `category`.
    pub fn get_item(&self, category: Category, name: &str) -> Result<&Value, CollectionError> {
        let details = self
            .categories
            .get(&category)
            .and_then(|items| items.get(name))
            .ok_or_else(|| CollectionError::ItemNotFound {
                category,
                name: name.to_owned(),
            })?;
        item_of_details(details, category, name)
    }

    /// Returns the first item (insertion order) in `category`.
    pub fn get_default_item(&self, category: Category) -> Result<&Value, CollectionError> {
        let items = self
            .categories
            .get(&category)
            .filter(|items| !items.is_empty())
            .ok_or(CollectionError::NoItems { category })?;
        let (name, details) = items.first().expect("filtered non-empty above");
        item_of_details(details, category, name)
    }

    /// Localizes `item`, wraps it in default item details, and stores it
    /// under `name` in `category` (replacing any existing entry in place).
    pub fn add_item(
        &mut self,
        category: Category,
        name: impl Into<String>,
        item: &Value,
    ) -> Result<(), CollectionError> {
        let details = make_item_details(item)?;
        self.categories
            .entry(category)
            .or_default()
            .insert(name.into(), details);
        Ok(())
    }

    /// Adds `data` to the repository under its SHA1 hex digest and returns
    /// the corresponding SRL.
    pub fn add_asset(&mut self, data: Vec<u8>) -> Srl {
        let hash = sha1_hex(&data);
        self.repository.insert(hash.clone(), data);
        Srl::for_hash(hash)
    }

    /// Loads an `.scp` archive (a zip) into this collection.
    ///
    /// Entries are processed in archive order. A leading `sonolus/` path
    /// component is stripped; directory entries, paths with fewer than two
    /// remaining components, and reserved file names (`info`/`list`,
    /// case-insensitive) are skipped. `repository/<name>` entries become
    /// repository blobs keyed by `<name>`; entries under a category
    /// directory are parsed as item-details JSON and stored under the file
    /// stem (entries that fail to parse are skipped, but the category is
    /// created regardless).
    pub fn load_from_scp(&mut self, zip_data: &[u8]) -> Result<(), CollectionError> {
        let mut archive = zip::ZipArchive::new(Cursor::new(zip_data))
            .map_err(|e| CollectionError::Zip(e.to_string()))?;
        for index in 0..archive.len() {
            let mut file = archive
                .by_index(index)
                .map_err(|e| CollectionError::Zip(e.to_string()))?;
            let raw_name = file.name().to_owned();
            if raw_name.ends_with('/') {
                continue;
            }
            let parts = zip_path_parts(&raw_name);
            let stripped: &[&str] = if parts.first().copied() == Some("sonolus") {
                &parts[1..]
            } else {
                &parts[..]
            };
            if stripped.len() < 2 {
                continue;
            }
            let file_name = *stripped.last().expect("len >= 2 checked above");
            if RESERVED_FILENAMES.contains(&file_name.to_lowercase().as_str()) {
                continue;
            }
            let dir_name = stripped[0];
            if dir_name == "repository" {
                let mut data = Vec::new();
                file.read_to_end(&mut data)
                    .map_err(|e| CollectionError::Zip(e.to_string()))?;
                self.repository.insert(file_name.to_owned(), data);
            } else if let Some(category) = Category::from_name(dir_name) {
                // The category exists even if the item JSON below is invalid.
                self.categories.entry(category).or_default();
                let mut data = Vec::new();
                file.read_to_end(&mut data)
                    .map_err(|e| CollectionError::Zip(e.to_string()))?;
                let Ok(details) = serde_json::from_slice::<Value>(&data) else {
                    continue;
                };
                let items = self
                    .categories
                    .get_mut(&category)
                    .expect("category created above");
                items.insert(python_stem(file_name).to_owned(), details);
            }
        }
        Ok(())
    }

    /// Loads a source resource tree: `<root>/<category>/<item>/item.json`
    /// plus sibling resource files. `.json`/`.bin` resources are gzipped
    /// (mtime 0); every resource is added to the repository and recorded on
    /// the item as an SRL under the resource file stem.
    ///
    /// Returns the warnings the Python reference would print (invalid item
    /// JSON, unreadable resources). Directory entries are visited in
    /// name-sorted order for deterministic output.
    pub fn load_from_source(&mut self, root: &Path) -> Result<Vec<String>, CollectionError> {
        let mut warnings = Vec::new();
        for category_dir in sorted_dir_entries(root)? {
            if !category_dir.is_dir() {
                continue;
            }
            let category_name = file_name_string(&category_dir);
            let Some(category) = Category::from_name(&category_name) else {
                continue;
            };
            for item_dir in sorted_dir_entries(&category_dir)? {
                if !item_dir.is_dir() {
                    continue;
                }
                let item_json_path = item_dir.join("item.json");
                if !item_json_path.exists() {
                    continue;
                }
                let bytes = fs::read(&item_json_path)?;
                let Ok(parsed) = serde_json::from_slice::<Value>(&bytes) else {
                    warnings.push(format!(
                        "Invalid JSON in {}, skipping item.",
                        item_json_path.display()
                    ));
                    continue;
                };
                let mut item = localize_item_map(&parsed)?;
                let item_name = file_name_string(&item_dir);
                item.insert("name".to_owned(), Value::String(item_name.clone()));
                for resource_path in sorted_dir_entries(&item_dir)? {
                    let resource_name = file_name_string(&resource_path);
                    if resource_name == "item.json" {
                        continue;
                    }
                    let data = match fs::read(&resource_path) {
                        Ok(data) => data,
                        Err(e) => {
                            warnings.push(format!(
                                "Error processing resource {}: {}",
                                resource_path.display(),
                                e
                            ));
                            continue;
                        }
                    };
                    let suffix = python_suffix(&resource_name).to_lowercase();
                    let data = if suffix == ".json" || suffix == ".bin" {
                        gzip_compress(&data)
                    } else {
                        data
                    };
                    let srl = self.add_asset(data);
                    item.insert(python_stem(&resource_name).to_owned(), srl.to_value());
                }
                self.add_item(category, item_name, &Value::Object(item))?;
            }
        }
        Ok(warnings)
    }

    /// Resolves references from levels to other items, in place:
    ///
    /// - a string `"engine"` is replaced by that engine's item (an error if
    ///   the engine does not exist);
    /// - for `useSkin`/`useBackground`/`useEffect`/`useParticle`, an inner
    ///   `"item"` that is a string naming an existing item, or an object
    ///   whose `"name"` names an existing item, is replaced by that item.
    ///   Anything else is left untouched.
    ///
    /// Called automatically by [`Collection::write`]; idempotent on success.
    pub fn link(&mut self) -> Result<(), CollectionError> {
        let level_names: Vec<String> = self
            .categories
            .get(&Category::Levels)
            .map(|items| items.keys().cloned().collect())
            .unwrap_or_default();
        for level_name in &level_names {
            // Engine: resolve only when the current value is a string.
            let engine_name = {
                let item = self.level_item(level_name)?;
                let engine = item.get("engine").ok_or_else(|| {
                    CollectionError::InvalidItem(format!(
                        "level '{level_name}' is missing required key 'engine'"
                    ))
                })?;
                engine.as_str().map(str::to_owned)
            };
            if let Some(engine_name) = engine_name {
                let engine_item = self.get_item(Category::Engines, &engine_name)?.clone();
                self.set_level_field(level_name, "engine", engine_item)?;
            }
            for (key, category) in [
                ("useSkin", Category::Skins),
                ("useBackground", Category::Backgrounds),
                ("useEffect", Category::Effects),
                ("useParticle", Category::Particles),
            ] {
                let target_name = {
                    let item = self.level_item(level_name)?;
                    let use_item = item.get(key).ok_or_else(|| {
                        CollectionError::InvalidItem(format!(
                            "level '{level_name}' is missing required key '{key}'"
                        ))
                    })?;
                    let Some(value) = use_item.as_object().and_then(|obj| obj.get("item")) else {
                        continue;
                    };
                    match value {
                        Value::String(name) if self.has_item(category, name) => Some(name.clone()),
                        Value::Object(obj) => match obj.get("name") {
                            Some(Value::String(name)) if self.has_item(category, name) => {
                                Some(name.clone())
                            }
                            _ => None,
                        },
                        _ => None,
                    }
                };
                if let Some(target_name) = target_name {
                    let target = self.get_item(category, &target_name)?.clone();
                    let item = self.level_item_object_mut(level_name)?;
                    let use_obj = item
                        .get_mut(key)
                        .and_then(Value::as_object_mut)
                        .expect("shape checked above");
                    use_obj.insert("item".to_owned(), target);
                }
            }
        }
        Ok(())
    }

    /// Links references, then writes the site tree under `path`:
    /// `sonolus/info`, `sonolus/<category>/{info,list,<item>...}` (skipping
    /// empty categories), and `sonolus/repository/<hash>` blobs.
    ///
    /// Repository files whose target already exists are skipped -- content is
    /// addressed by hash, so an existing file is already correct. All JSON is
    /// written in Python's `json.dumps` default format; output is
    /// deterministic (same collection in, same bytes out).
    pub fn write(&mut self, path: &Path) -> Result<WriteStats, CollectionError> {
        self.link()?;
        let base_dir = path.join(BASE_PATH.trim_matches('/'));
        fs::create_dir_all(&base_dir)?;
        let mut stats = WriteStats::default();
        self.write_main_info(&base_dir, &mut stats)?;
        self.write_category_items(&base_dir, &mut stats)?;
        self.write_repository_items(&base_dir, &mut stats)?;
        Ok(stats)
    }

    fn write_main_info(
        &self,
        base_dir: &Path,
        stats: &mut WriteStats,
    ) -> Result<(), CollectionError> {
        let mut sorted_categories: Vec<Category> = self.categories.keys().copied().collect();
        sorted_categories.sort_by_key(|category| category.sort_order());
        let buttons: Vec<Value> = sorted_categories
            .iter()
            .map(|category| json!({"type": category.singular()}))
            .collect();
        let info = json!({
            "title": self.name,
            "buttons": buttons,
            "configuration": {"options": []},
        });
        write_json_file(&base_dir.join("info"), &info, stats)
    }

    fn write_category_items(
        &self,
        base_dir: &Path,
        stats: &mut WriteStats,
    ) -> Result<(), CollectionError> {
        for (category, items) in &self.categories {
            if items.is_empty() {
                continue;
            }
            let category_dir = base_dir.join(category.as_str());
            fs::create_dir_all(&category_dir)?;
            let item_values: Vec<&Value> = items
                .iter()
                .map(|(name, details)| item_of_details(details, *category, name))
                .collect::<Result<_, _>>()?;
            let info = json!({
                "sections": [{
                    "itemType": category.singular(),
                    "title": "Items",
                    "items": item_values,
                }],
            });
            write_json_file(&category_dir.join("info"), &info, stats)?;
            let list = json!({"pageCount": 1, "items": item_values});
            write_json_file(&category_dir.join("list"), &list, stats)?;
            for (item_name, details) in items {
                write_json_file(&category_dir.join(item_name), details, stats)?;
            }
        }
        Ok(())
    }

    fn write_repository_items(
        &self,
        base_dir: &Path,
        stats: &mut WriteStats,
    ) -> Result<(), CollectionError> {
        let repo_dir = base_dir.join("repository");
        fs::create_dir_all(&repo_dir)?;
        for (key, data) in &self.repository {
            let target_path = repo_dir.join(key);
            if target_path.exists() {
                // Content is identified by its hash; a matching file can be
                // skipped (and is deliberately never rewritten).
                stats.repository_files_skipped += 1;
                continue;
            }
            fs::write(&target_path, data)?;
            stats.repository_files_written += 1;
        }
        Ok(())
    }

    /// Consumes the collection into its categories and repository (the T5.2
    /// FFI bridge: the `PyO3` layer keeps the repository persistently and
    /// hands the categories to Python, which owns them between operations).
    pub fn into_parts(self) -> (Categories, Repository) {
        (self.categories, self.repository)
    }

    /// The categories as a `{category: {name: details}}` JSON object.
    /// Insertion order is preserved at both levels and empty categories are
    /// included (they carry a main-info button even with no items).
    pub fn categories_value(&self) -> Value {
        let mut out = Map::new();
        for (category, items) in &self.categories {
            let mut items_map = Map::new();
            for (name, details) in items {
                items_map.insert(name.clone(), details.clone());
            }
            out.insert(category.as_str().to_owned(), Value::Object(items_map));
        }
        Value::Object(out)
    }

    /// Replaces the categories from a `{category: {name: details}}` JSON
    /// object (the inverse of [`Collection::categories_value`]). Unknown
    /// category names and non-object shapes are errors (the Python reference
    /// can only reach `write` with valid category keys; junk crashes it too).
    pub fn set_categories_from_value(&mut self, value: &Value) -> Result<(), CollectionError> {
        let Some(map) = value.as_object() else {
            return Err(CollectionError::InvalidItem(
                "categories must be a JSON object".to_owned(),
            ));
        };
        let mut categories = Categories::new();
        for (category_name, items_value) in map {
            let Some(category) = Category::from_name(category_name) else {
                return Err(CollectionError::InvalidItem(format!(
                    "unknown category '{category_name}'"
                )));
            };
            let Some(items) = items_value.as_object() else {
                return Err(CollectionError::InvalidItem(format!(
                    "items for category '{category_name}' must be a JSON object"
                )));
            };
            let entry = categories.entry(category).or_default();
            for (name, details) in items {
                entry.insert(name.clone(), details.clone());
            }
        }
        self.categories = categories;
        Ok(())
    }

    /// Merges `other` into this collection: repository blobs and item
    /// details overwrite same-keyed entries (keeping their original
    /// position), and new entries append. The name is not copied.
    pub fn update_from(&mut self, other: &Collection) {
        for (hash, data) in &other.repository {
            self.repository.insert(hash.clone(), data.clone());
        }
        for (category, items) in &other.categories {
            let target = self.categories.entry(*category).or_default();
            for (name, details) in items {
                target.insert(name.clone(), details.clone());
            }
        }
    }

    /// Iterates categories (insertion order) with their item maps.
    pub fn categories(&self) -> impl Iterator<Item = (Category, &IndexMap<String, Value>)> {
        self.categories
            .iter()
            .map(|(category, items)| (*category, items))
    }

    /// The item-details map for `category`, if the category exists.
    pub fn items(&self, category: Category) -> Option<&IndexMap<String, Value>> {
        self.categories.get(&category)
    }

    /// Mutable access to the item-details map for `category`.
    pub fn items_mut(&mut self, category: Category) -> Option<&mut IndexMap<String, Value>> {
        self.categories.get_mut(&category)
    }

    /// The item-details map for `category`, created if missing (the
    /// `setdefault` shape T5.2's dict-like API needs).
    pub fn ensure_category(&mut self, category: Category) -> &mut IndexMap<String, Value> {
        self.categories.entry(category).or_default()
    }

    /// The content-addressed blob repository.
    pub fn repository(&self) -> &IndexMap<String, Vec<u8>> {
        &self.repository
    }

    /// Mutable access to the blob repository.
    pub fn repository_mut(&mut self) -> &mut IndexMap<String, Vec<u8>> {
        &mut self.repository
    }

    fn has_item(&self, category: Category, name: &str) -> bool {
        self.categories
            .get(&category)
            .is_some_and(|items| items.contains_key(name))
    }

    /// The `"item"` of the stored details for a level (shared lookup for
    /// `link`).
    fn level_item(&self, level_name: &str) -> Result<&Value, CollectionError> {
        let details = self
            .categories
            .get(&Category::Levels)
            .and_then(|items| items.get(level_name))
            .ok_or_else(|| CollectionError::ItemNotFound {
                category: Category::Levels,
                name: level_name.to_owned(),
            })?;
        item_of_details(details, Category::Levels, level_name)
    }

    fn level_item_object_mut(
        &mut self,
        level_name: &str,
    ) -> Result<&mut Map<String, Value>, CollectionError> {
        let details = self
            .categories
            .get_mut(&Category::Levels)
            .and_then(|items| items.get_mut(level_name))
            .ok_or_else(|| CollectionError::ItemNotFound {
                category: Category::Levels,
                name: level_name.to_owned(),
            })?;
        details
            .get_mut("item")
            .and_then(Value::as_object_mut)
            .ok_or_else(|| {
                CollectionError::InvalidItem(format!(
                    "item details for '{level_name}' in category 'levels' have no 'item' object"
                ))
            })
    }

    fn set_level_field(
        &mut self,
        level_name: &str,
        key: &str,
        value: Value,
    ) -> Result<(), CollectionError> {
        self.level_item_object_mut(level_name)?
            .insert(key.to_owned(), value);
        Ok(())
    }
}

/// Wraps a (localized) item in the default details shape:
/// `{"item": ..., "actions": [], "hasCommunity": false, "leaderboards": [],
/// "sections": []}`.
pub fn make_item_details(item: &Value) -> Result<Value, CollectionError> {
    let localized = localize_item(item)?;
    Ok(json!({
        "item": localized,
        "actions": [],
        "hasCommunity": false,
        "leaderboards": [],
        "sections": [],
    }))
}

/// Localizes an item object: each [`LOCALIZED_KEYS`] field present is
/// flattened with [`localize_text`], each entry of a `"tags"` array gets its
/// `"title"` localized, and a `"meta"` key is removed. Key order is
/// preserved. Idempotent.
pub fn localize_item(item: &Value) -> Result<Value, CollectionError> {
    localize_item_map(item).map(Value::Object)
}

fn localize_item_map(item: &Value) -> Result<Map<String, Value>, CollectionError> {
    let Some(source) = item.as_object() else {
        return Err(CollectionError::InvalidItem(
            "an item must be a JSON object".to_owned(),
        ));
    };
    let mut localized = source.clone();
    for key in LOCALIZED_KEYS {
        if let Some(value) = localized.get_mut(key) {
            *value = localize_text(value);
        }
    }
    if let Some(tags) = localized.get_mut("tags") {
        let Value::Array(tags) = tags else {
            return Err(CollectionError::InvalidItem(
                "item 'tags' must be an array".to_owned(),
            ));
        };
        for tag in tags.iter_mut() {
            let Value::Object(tag) = tag else {
                return Err(CollectionError::InvalidItem(
                    "item tags must be objects".to_owned(),
                ));
            };
            let Some(title) = tag.get("title") else {
                return Err(CollectionError::InvalidItem(
                    "item tag is missing 'title'".to_owned(),
                ));
            };
            let localized_title = localize_text(title);
            tag.insert("title".to_owned(), localized_title);
        }
    }
    localized.shift_remove("meta");
    Ok(localized)
}

/// Flattens localized text: a string is returned as-is; an object with an
/// `"en"` key yields that value; any other non-empty object yields the value
/// of its smallest key (code-point order); everything else becomes `""`.
pub fn localize_text(text: &Value) -> Value {
    match text {
        Value::String(_) => text.clone(),
        Value::Object(map) if map.contains_key("en") => map["en"].clone(),
        Value::Object(map) if !map.is_empty() => {
            let min_key = map.keys().min().expect("checked non-empty");
            map[min_key].clone()
        }
        _ => Value::String(String::new()),
    }
}

/// The lowercase SHA1 hex digest of `data` (repository content addressing).
pub fn sha1_hex(data: &[u8]) -> String {
    use std::fmt::Write as _;

    let digest = Sha1::digest(data);
    let mut out = String::with_capacity(40);
    for byte in digest {
        let _ = write!(out, "{byte:02x}");
    }
    out
}

/// Gzips `data` with mtime 0 so the output is reproducible (same input in,
/// same bytes out). Matching Python's `gzip.compress(data, mtime=0)`
/// *content* is the contract, not its exact bytes (ARCHITECTURE.md section
/// 7) -- though with the zlib-rs backend at level 9 the bytes match the
/// `CPython` output in practice.
pub fn gzip_compress(data: &[u8]) -> Vec<u8> {
    let mut encoder = flate2::GzBuilder::new().mtime(0).write(
        Vec::with_capacity(data.len() / 2),
        flate2::Compression::new(9),
    );
    encoder
        .write_all(data)
        .expect("writing to an in-memory gzip encoder cannot fail");
    encoder
        .finish()
        .expect("finishing an in-memory gzip encoder cannot fail")
}

/// Decompresses a gzip stream (testing/inspection helper; the inverse of
/// [`gzip_compress`]).
pub fn gzip_decompress(data: &[u8]) -> std::io::Result<Vec<u8>> {
    let mut out = Vec::new();
    flate2::read::GzDecoder::new(data).read_to_end(&mut out)?;
    Ok(out)
}

fn item_of_details<'a>(
    details: &'a Value,
    category: Category,
    name: &str,
) -> Result<&'a Value, CollectionError> {
    details.get("item").ok_or_else(|| {
        CollectionError::InvalidItem(format!(
            "item details for '{name}' in category '{category}' have no 'item' field"
        ))
    })
}

fn write_json_file(
    path: &Path,
    value: &Value,
    stats: &mut WriteStats,
) -> Result<(), CollectionError> {
    fs::write(path, pyjson::dumps(value))?;
    stats.json_files_written += 1;
    Ok(())
}

/// Splits a zip entry name into path components the way `pathlib.Path`
/// does on Windows (both separators), dropping empty and `.` components
/// (`..` is preserved, matching `PurePath.parts`).
fn zip_path_parts(name: &str) -> Vec<&str> {
    name.split(['/', '\\'])
        .filter(|part| !part.is_empty() && *part != ".")
        .collect()
}

/// `PurePath.stem` for a final path component, per `CPython` 3.14 semantics
/// (`os.path.splitext`): the suffix starts at the last `.` unless every
/// character before it is also a `.` (so `.gitignore` and `..` have no
/// suffix, while `foo.` does).
fn python_stem(name: &str) -> &str {
    let Some(dot) = name.rfind('.') else {
        return name;
    };
    if name[..dot].bytes().all(|b| b == b'.') {
        return name;
    }
    &name[..dot]
}

/// `PurePath.suffix` counterpart of [`python_stem`].
fn python_suffix(name: &str) -> &str {
    let Some(dot) = name.rfind('.') else {
        return "";
    };
    if name[..dot].bytes().all(|b| b == b'.') {
        return "";
    }
    &name[dot..]
}

/// Directory entries sorted by file name. Python's `iterdir` yields OS
/// enumeration order; sorting makes source-tree loads deterministic
/// regardless of filesystem (invariant 5).
fn sorted_dir_entries(dir: &Path) -> std::io::Result<Vec<PathBuf>> {
    let mut entries: Vec<PathBuf> = fs::read_dir(dir)?
        .map(|entry| entry.map(|e| e.path()))
        .collect::<std::io::Result<_>>()?;
    entries.sort_by(|a, b| a.file_name().cmp(&b.file_name()));
    Ok(entries)
}

fn file_name_string(path: &Path) -> String {
    path.file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn category_round_trips_names_and_orders() {
        for category in Category::ALL {
            assert_eq!(Category::from_name(category.as_str()), Some(category));
        }
        assert_eq!(Category::from_name("Levels"), None);
        assert_eq!(Category::from_name("repository"), None);
        let mut sorted = Category::ALL.to_vec();
        sorted.sort_by_key(|c| c.sort_order());
        let names: Vec<&str> = sorted.iter().map(|c| c.as_str()).collect();
        assert_eq!(
            names,
            [
                "levels",
                "engines",
                "skins",
                "effects",
                "particles",
                "backgrounds",
                "posts",
                "playlists",
                "replays",
            ]
        );
    }

    #[test]
    fn sha1_matches_known_vector() {
        assert_eq!(sha1_hex(b"abc"), "a9993e364706816aba3e25717850c26c9cd0d89d");
        assert_eq!(sha1_hex(b""), "da39a3ee5e6b4b0d3255bfef95601890afd80709");
    }

    #[test]
    fn add_asset_uses_sha1_and_base_path() {
        let mut collection = Collection::new();
        let srl = collection.add_asset(b"abc".to_vec());
        assert_eq!(srl.hash, "a9993e364706816aba3e25717850c26c9cd0d89d");
        assert_eq!(
            srl.url,
            "/sonolus/repository/a9993e364706816aba3e25717850c26c9cd0d89d"
        );
        assert_eq!(
            collection.repository().get(&srl.hash).map(Vec::as_slice),
            Some(&b"abc"[..])
        );
        let value = srl.to_value();
        assert_eq!(
            pyjson::dumps(&value),
            format!(
                r#"{{"hash": "{0}", "url": "/sonolus/repository/{0}"}}"#,
                srl.hash
            )
        );
    }

    #[test]
    fn gzip_is_reproducible_with_zero_mtime() {
        let data = b"some resource data that should compress fine".repeat(8);
        let a = gzip_compress(&data);
        let b = gzip_compress(&data);
        assert_eq!(a, b, "same input must produce identical gzip bytes");
        // Gzip header: magic, deflate method, flags, then 4 mtime bytes.
        assert_eq!(&a[..3], &[0x1f, 0x8b, 0x08]);
        assert_eq!(&a[4..8], &[0, 0, 0, 0], "mtime must be zero");
        assert_eq!(gzip_decompress(&a).unwrap(), data);
    }

    #[test]
    fn localize_text_follows_python_fallbacks() {
        use serde_json::json;

        // Captured from the frozen Python implementation.
        assert_eq!(localize_text(&json!("plain")), json!("plain"));
        assert_eq!(
            localize_text(&json!({"en": "english", "ja": "x"})),
            json!("english")
        );
        assert_eq!(
            localize_text(&json!({"fr": "bonjour", "de": "hallo"})),
            json!("hallo"),
            "smallest language key wins when 'en' is absent"
        );
        assert_eq!(localize_text(&json!({})), json!(""));
        assert_eq!(localize_text(&json!(42)), json!(""));
        assert_eq!(localize_text(&json!(null)), json!(""));
        assert_eq!(localize_text(&json!(["x"])), json!(""));
        // The 'en' value is passed through even when it is not a string.
        assert_eq!(localize_text(&json!({"en": 5})), json!(5));
    }

    #[test]
    fn localize_item_matches_python_reference() {
        use serde_json::json;

        let item = json!({
            "title": {"ja": "x", "en": "y"},
            "meta": {"k": 1},
            "tags": [{"title": {"en": "t1"}, "extra": 2}],
            "other": 3,
        });
        let localized = localize_item(&item).unwrap();
        // Captured verbatim from the frozen Python implementation.
        assert_eq!(
            pyjson::dumps(&localized),
            r#"{"title": "y", "tags": [{"title": "t1", "extra": 2}], "other": 3}"#
        );
        // Idempotent.
        let again = localize_item(&localized).unwrap();
        assert_eq!(again, localized);
    }

    #[test]
    fn localize_item_rejects_malformed_shapes() {
        use serde_json::json;

        assert!(localize_item(&json!("not an object")).is_err());
        assert!(localize_item(&json!({"tags": "not an array"})).is_err());
        assert!(localize_item(&json!({"tags": ["not an object"]})).is_err());
        // Python raises KeyError('title') here.
        assert!(localize_item(&json!({"tags": [{"notitle": 1}]})).is_err());
    }

    #[test]
    fn make_item_details_has_the_legacy_shape_and_key_order() {
        use serde_json::json;

        let details = make_item_details(&json!({"title": "t"})).unwrap();
        assert_eq!(
            pyjson::dumps(&details),
            r#"{"item": {"title": "t"}, "actions": [], "hasCommunity": false, "leaderboards": [], "sections": []}"#
        );
    }

    #[test]
    fn get_item_errors_match_python_keyerror_messages() {
        let collection = Collection::new();
        let err = collection.get_item(Category::Skins, "nope").unwrap_err();
        assert_eq!(err.to_string(), "Item 'nope' not found in category 'skins'");
        let err = collection.get_default_item(Category::Skins).unwrap_err();
        assert_eq!(err.to_string(), "No items found in category 'skins'");
    }

    #[test]
    fn get_default_item_returns_first_inserted() {
        use serde_json::json;

        let mut collection = Collection::new();
        collection
            .add_item(Category::Skins, "first", &json!({"name": "first"}))
            .unwrap();
        collection
            .add_item(Category::Skins, "second", &json!({"name": "second"}))
            .unwrap();
        assert_eq!(
            collection.get_default_item(Category::Skins).unwrap()["name"],
            json!("first")
        );
    }

    #[test]
    fn stem_and_suffix_match_cpython_314() {
        // Table captured from CPython 3.14 PurePosixPath.
        let stems = [
            ("foo", "foo"),
            ("foo.json", "foo"),
            (".gitignore", ".gitignore"),
            ("foo.", "foo"),
            ("foo.tar.gz", "foo.tar"),
            ("foo..", "foo."),
            ("..", ".."),
            ("a..b", "a."),
            ("x.y.", "x.y"),
            ("....", "...."),
        ];
        for (name, expected) in stems {
            assert_eq!(python_stem(name), expected, "stem of {name:?}");
        }
        let suffixes = [
            ("foo", ""),
            ("foo.json", ".json"),
            (".gitignore", ""),
            ("foo.", "."),
            ("foo.JSON", ".JSON"),
            ("a.", "."),
            ("..", ""),
        ];
        for (name, expected) in suffixes {
            assert_eq!(python_suffix(name), expected, "suffix of {name:?}");
        }
    }

    #[test]
    fn zip_path_parts_match_pathlib() {
        assert_eq!(
            zip_path_parts("sonolus/levels/x"),
            ["sonolus", "levels", "x"]
        );
        assert_eq!(zip_path_parts("./levels/x"), ["levels", "x"]);
        assert_eq!(zip_path_parts("levels//x"), ["levels", "x"]);
        assert_eq!(zip_path_parts("levels/./x"), ["levels", "x"]);
        assert_eq!(zip_path_parts("levels\\x"), ["levels", "x"]);
        assert_eq!(zip_path_parts("levels/.."), ["levels", ".."]);
        assert_eq!(zip_path_parts(""), Vec::<&str>::new());
    }

    #[test]
    fn categories_value_round_trips_through_set_categories() {
        use serde_json::json;

        let mut original = Collection::new();
        original
            .add_item(Category::Levels, "l1", &json!({"name": "l1", "v": 1}))
            .unwrap();
        original
            .add_item(Category::Skins, "s2", &json!({"name": "s2"}))
            .unwrap();
        original
            .add_item(Category::Skins, "s1", &json!({"name": "s1"}))
            .unwrap();
        // An empty category still carries a main-info button and must survive.
        original.ensure_category(Category::Posts);

        let value = original.categories_value();
        let mut restored = Collection::new();
        restored.set_categories_from_value(&value).unwrap();
        assert_eq!(
            restored
                .categories()
                .map(|(c, _)| c)
                .collect::<Vec<Category>>(),
            [Category::Levels, Category::Skins, Category::Posts],
            "category insertion order is preserved"
        );
        assert_eq!(
            restored
                .items(Category::Skins)
                .unwrap()
                .keys()
                .collect::<Vec<_>>(),
            ["s2", "s1"],
            "item insertion order is preserved"
        );
        assert_eq!(pyjson::dumps(&restored.categories_value()), {
            pyjson::dumps(&value)
        });

        // set_categories_from_value replaces, not merges.
        restored
            .set_categories_from_value(&json!({"engines": {}}))
            .unwrap();
        assert_eq!(
            restored
                .categories()
                .map(|(c, _)| c)
                .collect::<Vec<Category>>(),
            [Category::Engines]
        );
    }

    #[test]
    fn set_categories_from_value_rejects_malformed_shapes() {
        use serde_json::json;

        let mut collection = Collection::new();
        assert!(collection.set_categories_from_value(&json!([])).is_err());
        assert!(
            collection
                .set_categories_from_value(&json!({"nonsense": {}}))
                .is_err()
        );
        assert!(
            collection
                .set_categories_from_value(&json!({"levels": []}))
                .is_err()
        );
    }

    #[test]
    fn into_parts_returns_categories_and_repository() {
        use serde_json::json;

        let mut collection = Collection::new();
        collection
            .add_item(Category::Levels, "l", &json!({"name": "l"}))
            .unwrap();
        let srl = collection.add_asset(b"blob".to_vec());
        let (categories, repository) = collection.into_parts();
        assert_eq!(categories.len(), 1);
        assert!(categories[&Category::Levels].contains_key("l"));
        assert_eq!(
            repository.get(&srl.hash).map(Vec::as_slice),
            Some(&b"blob"[..])
        );
    }

    #[test]
    fn update_from_overwrites_in_place_and_appends() {
        use serde_json::json;

        let mut a = Collection::new();
        a.add_item(Category::Skins, "s1", &json!({"v": 1})).unwrap();
        a.add_item(Category::Skins, "s2", &json!({"v": 2})).unwrap();
        a.add_asset(b"one".to_vec());

        let mut b = Collection::new();
        b.name = "Other".to_owned();
        b.add_item(Category::Skins, "s1", &json!({"v": 10}))
            .unwrap();
        b.add_item(Category::Levels, "l1", &json!({"v": 3}))
            .unwrap();
        b.add_asset(b"two".to_vec());

        a.update_from(&b);
        assert_eq!(a.name, "Unnamed", "update does not copy the name");
        let skins = a.items(Category::Skins).unwrap();
        assert_eq!(
            skins.keys().collect::<Vec<_>>(),
            ["s1", "s2"],
            "overwritten keys keep their original position"
        );
        assert_eq!(skins["s1"]["item"]["v"], json!(10));
        assert!(a.items(Category::Levels).is_some());
        assert_eq!(a.repository().len(), 2);
    }
}
