from __future__ import annotations

import hashlib
import json
import urllib.request
import zipfile
from io import BytesIO
from os import PathLike
from pathlib import Path
from typing import Any, ClassVar, Literal, TypedDict, cast

Category = Literal["post", "playlist", "level", "replay", "skin", "background", "effect", "particle", "engine"]

Asset = bytes | PathLike | str


class Collection:
    BASE_PATH = "/sonolus/"
    RESERVED_FILENAMES = frozenset(("info", "list"))

    CATEGORY_PLURALS: ClassVar[dict[Category, str]] = {
        "post": "posts",
        "playlist": "playlists",
        "level": "levels",
        "replay": "replays",
        "skin": "skins",
        "background": "backgrounds",
        "effect": "effects",
        "particle": "particles",
        "engine": "engines",
    }

    def __init__(self) -> None:
        self.categories: dict[Category, dict[str, Any]] = {}
        self.repository: dict[str, bytes] = {}

    def get_item(self, category: Category, name: str) -> Any:
        if name not in self.categories.get(category, {}):
            raise KeyError(f"Item '{name}' not found in category '{category}'")
        return self.categories[category][name]

    def get_default_item(self, category: Category) -> Any:
        if not self.categories.get(category):
            raise KeyError(f"No items found in category '{category}'")
        return next(iter(self.categories[category].values()))

    def add_item(self, category: Category, name: str, item: Any) -> None:
        self.categories.setdefault(category, {})[name] = {"item": item}

    @staticmethod
    def _load_data(value: Asset) -> bytes:
        match value:
            case str() if value.startswith(("http://", "https://")):
                with urllib.request.urlopen(value) as response:
                    return response.read()
            case PathLike():
                return Path(value).read_bytes()
            case bytes():
                return value
            case _:
                raise TypeError("value must be a URL, a path, or bytes")

    def add_asset(self, value: Asset, /) -> Srl:
        data = self._load_data(value)
        key = hashlib.sha1(data).hexdigest()
        self.repository[key] = data
        return Srl(hash=key, url=f"{self.BASE_PATH}repository/{key}")

    @classmethod
    def from_scp(cls, zip_data: Asset) -> Collection:
        collection = cls()
        collection.load_from_scp(zip_data)
        return collection

    def load_from_scp(self, zip_data: Asset) -> None:
        with zipfile.ZipFile(BytesIO(self._load_data(zip_data))) as zf:
            files_by_dir = self._group_zip_entries_by_directory(zf.filelist)
            self._process_zip_directories(zf, files_by_dir)

    def _group_zip_entries_by_directory(self, file_list: list[zipfile.ZipInfo]) -> dict[str, list[zipfile.ZipInfo]]:
        files_by_dir: dict[str, list[zipfile.ZipInfo]] = {}

        for zip_entry in file_list:
            if self._should_skip_zip_entry(zip_entry):
                continue

            dir_name = Path(zip_entry.filename).parts[0]
            files_by_dir.setdefault(dir_name, []).append(zip_entry)

        return files_by_dir

    def _should_skip_zip_entry(self, zip_entry: zipfile.ZipInfo) -> bool:
        path = Path(zip_entry.filename)
        return zip_entry.filename.endswith("/") or len(path.parts) < 2 or path.name.lower() in self.RESERVED_FILENAMES

    def _process_zip_directories(self, zf: zipfile.ZipFile, files_by_dir: dict[str, list[zipfile.ZipInfo]]) -> None:
        for dir_name, zip_entries in files_by_dir.items():
            if dir_name == "configuration":
                continue
            if self._is_valid_category(dir_name):
                self.categories.setdefault(dir_name, {})  # type: ignore
                self._extract_category_items(zf, dir_name, zip_entries)

    def _is_valid_category(self, category: str) -> bool:
        return category in self.CATEGORY_PLURALS

    def _extract_category_items(self, zf: zipfile.ZipFile, dir_name: str, zip_entries: list[zipfile.ZipInfo]) -> None:
        assert dir_name in self.CATEGORY_PLURALS
        dir_name = cast(Category, dir_name)
        for zip_entry in zip_entries:
            try:
                item_details = json.loads(zf.read(zip_entry))
            except json.JSONDecodeError:
                continue

            item_name = Path(zip_entry.filename).stem
            if self._is_valid_category(dir_name):
                self.categories[dir_name][item_name] = {"item": item_details}

    def save(self, path: Asset) -> None:
        base_dir = self._create_base_directory(path)
        self._write_main_info(base_dir)
        self._write_category_items(base_dir)
        self._write_repository_items(base_dir)

    def _create_base_directory(self, path: Asset) -> Path:
        base_dir = Path(path) / self.BASE_PATH.strip("/")
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir

    def _write_main_info(self, base_dir: Path) -> None:
        info = {
            "title": "Sonolus.py Collection",
            "buttons": [{"type": category} for category, values in self.categories.items() if values],
        }
        self._write_json(base_dir / "info", info)

    def _write_category_items(self, base_dir: Path) -> None:
        for category, items in self.categories.items():
            if not items:
                continue
            category_dir = self._create_category_directory(base_dir, category)
            self._write_category_structure(category_dir, items)

    def _create_category_directory(self, base_dir: Path, category: Category) -> Path:
        plural = self.CATEGORY_PLURALS[category]
        category_dir = base_dir / plural
        category_dir.mkdir(exist_ok=True)
        return category_dir

    def _write_category_structure(self, category_dir: Path, items: dict[str, Any]) -> None:
        self._write_json(category_dir / "info", {"sections": []})

        category_list = {"pageCount": 1, "items": [item_details["item"] for item_details in items.values()]}
        self._write_json(category_dir / "list", category_list)

        for item_name, item_details in items.items():
            self._write_json(category_dir / item_name, item_details["item"])

    def _write_repository_items(self, base_dir: Path) -> None:
        repo_dir = base_dir / "repository"
        repo_dir.mkdir(exist_ok=True)

        for key, data in self.repository.items():
            (repo_dir / key).write_bytes(data)

    @staticmethod
    def _write_json(path: Path, content: Any) -> None:
        path.write_text(json.dumps(content))

    def update(self, other: Collection) -> None:
        self.repository.update(other.repository)
        for category, items in other.categories.items():
            self.categories.setdefault(category, {}).update(items)


class Srl(TypedDict):
    hash: str
    url: str
