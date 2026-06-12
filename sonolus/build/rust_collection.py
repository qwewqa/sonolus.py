"""Rust-backed Collection (PORT.md task T5.2).

:class:`RustCollection` is a drop-in replacement for
:class:`sonolus.build.collection.Collection` backed by the Rust collection core
(`sonolus_backend.Collection`). The split of responsibilities follows
ARCHITECTURE.md section 7:

- Python keeps orchestration: ``name`` and the ``categories`` item dicts (plain
  Python dicts that the build pipeline mutates in place, exactly like the
  legacy class — the dict-manipulation methods such as ``get_item``/``add_item``
  and item localization are inherited unchanged), user level-converter
  callbacks, and URL fetching (assets reach Rust as bytes).
- Rust holds the SHA1 content-addressed blob repository and performs scp/zip
  parsing, SHA1 hashing, resource gzip, level/engine linking, CPython-exact
  JSON serialization, and the site-tree write with skip-if-hash-exists.

Categories cross the FFI as JSON strings. ``write`` links the snapshot in Rust
and the linked snapshot is adopted back into ``self.categories`` so the
post-write state evolves exactly like the legacy in-place ``link`` (the dev
server rebuilds against that state).

Documented divergences from the legacy class (garbage-input/error paths only;
see also the T5.1 notes in ``rust/sonolus-backend-core/src/collection/mod.rs``):

- malformed scp bytes raise ``ValueError`` instead of ``zipfile.BadZipFile``,
  and malformed item shapes during ``write``/link raise ``KeyError``/
  ``ValueError`` with messages that match the legacy ones where the message is
  observable behavior (missing items) but not everywhere (missing ``use*``
  keys);
- ``load_from_source`` visits directories in name-sorted order (deterministic)
  instead of OS enumeration order, and re-emits its warnings/prints after the
  load instead of interleaved with it. Iteration order reaches output: resource
  SRL keys are inserted into the item dict in visit order, so written item JSON
  key order matches the legacy class only where OS enumeration happens to be
  name-sorted (NTFS yes, ext4 no — content is structurally identical either
  way);
- item values must round-trip through JSON: integers beyond 64 bits and
  non-finite floats are not supported (the legacy class would serialize them).
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Iterator, MutableMapping
from os import PathLike
from pathlib import Path
from typing import Any

import sonolus_backend

from sonolus.build.collection import Asset, Collection, Srl


class RustRepository(MutableMapping[str, bytes]):
    """``dict[str, bytes]``-compatible view over the Rust-held blob repository."""

    def __init__(self, core: sonolus_backend.Collection) -> None:
        self._core = core

    def __getitem__(self, key: str) -> bytes:
        value = self._core.repository_get(key)
        if value is None:
            raise KeyError(key)
        return value

    def __setitem__(self, key: str, value: bytes) -> None:
        self._core.repository_set(key, bytes(value))

    def __delitem__(self, key: str) -> None:
        if not self._core.repository_remove(key):
            raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._core.repository_keys())

    def __len__(self) -> int:
        return self._core.repository_len()

    def __repr__(self) -> str:
        return f"RustRepository(<{len(self)} blobs>)"


class RustCollection(Collection):
    """Drop-in :class:`Collection` backed by the Rust collection core."""

    def __init__(self) -> None:
        super().__init__()
        self._core = sonolus_backend.Collection()
        # The legacy plain-dict repository is replaced by the Rust-backed view;
        # every inherited method that touched it is overridden below.
        self.repository = RustRepository(self._core)  # type: ignore[assignment]
        self._last_write_stats: dict[str, int] | None = None

    def add_asset(self, value: Asset, /) -> Srl:
        # Asset loading (URL fetch / file read) stays in Python; Rust hashes
        # and stores the bytes.
        data = self._load_data(value)
        key, url = self._core.add_asset(data)
        return Srl(hash=key, url=url)

    def load_from_scp(self, zip_data: Asset) -> None:
        categories_json = self._core.load_from_scp(self._load_data(zip_data))
        self._merge_categories(json.loads(categories_json))

    def load_from_source(self, path: PathLike | str) -> None:
        categories_json, messages = self._core.load_from_source(str(Path(path)))
        for message in messages:
            if message.startswith("Invalid JSON in"):
                warnings.warn(message, stacklevel=2)
            else:
                print(message)
        self._merge_categories(json.loads(categories_json))

    def _merge_categories(self, parsed: dict[str, dict[str, Any]]) -> None:
        for category, items in parsed.items():
            self.categories.setdefault(category, {}).update(items)

    def write(self, path: Asset) -> None:
        stats, linked_json = self._core.write(str(Path(path)), self.name, json.dumps(self.categories))
        # Adopt the post-link snapshot so the in-memory state evolves exactly
        # like the legacy in-place link() (dev-server rebuilds depend on it).
        self.categories.clear()
        self.categories.update(json.loads(linked_json))
        self._last_write_stats = stats

    def update(self, other: Collection) -> None:
        if other is self:
            return
        if isinstance(other, RustCollection):
            self._core.update_from(other._core)
        else:
            for key, data in other.repository.items():
                self._core.repository_set(key, data)
        for category, items in other.categories.items():
            self.categories.setdefault(category, {}).update(items)
