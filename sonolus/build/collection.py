from __future__ import annotations

import gzip
import hashlib
import json
import urllib.request
import zipfile
from io import BytesIO
from os import PathLike
from pathlib import Path
from typing import Any, Literal, TypedDict, TypeGuard

type Category = Literal[
    "posts",
    "playlists",
    "levels",
    "replays",
    "skins",
    "backgrounds",
    "effects",
    "particles",
    "engines",
]
type Asset = bytes | PathLike | str
CATEGORY_NAMES = {"posts", "playlists", "levels", "replays", "skins", "backgrounds", "effects", "particles", "engines"}
SINGULAR_CATEGORY_NAMES: dict[Category, str] = {
    "posts": "post",
    "playlists": "playlist",
    "levels": "level",
    "replays": "replay",
    "skins": "skin",
    "backgrounds": "background",
    "effects": "effect",
    "particles": "particle",
    "engines": "engine",
}
BASE_PATH = "/sonolus/"
RESERVED_FILENAMES = {"info", "list"}
LOCALIZED_KEYS = {"title", "subtitle", "author", "description", "artists"}
CATEGORY_SORT_ORDER = {
    "levels": 0,
    "engines": 1,
    "skins": 2,
    "effects": 3,
    "particles": 4,
    "backgrounds": 5,
    "posts": 6,
    "playlists": 7,
    "replays": 8,
}


class Collection:
    def __init__(self) -> None:
        self.name = "Unnamed"
        self.categories: dict[Category, dict[str, Any]] = {}
        self.repository: dict[str, bytes] = {}

    def get_item(self, category: Category, name: str) -> Any:
        if name not in self.categories.get(category, {}):
            raise KeyError(f"Item '{name}' not found in category '{category}'")
        return self.categories[category][name]["item"]

    def get_default_item(self, category: Category) -> Any:
        if not self.categories.get(category):
            raise KeyError(f"No items found in category '{category}'")
        return next(iter(self.categories[category].values()))["item"]

    def add_item(self, category: Category, name: str, item: Any) -> None:
        self.categories.setdefault(category, {})[name] = self._make_item_details(item)

    @classmethod
    def _make_item_details(cls, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "item": cls._localize_item(item),
            "actions": [],
            "hasCommunity": False,
            "leaderboards": [],
            "sections": [],
        }

    @staticmethod
    def _load_data(value: Asset) -> bytes:
        return load_asset(value)

    def add_asset(self, value: Asset, /) -> Srl:
        data = self._load_data(value)
        key = hashlib.sha1(data).hexdigest()
        self.repository[key] = data
        return Srl(hash=key, url=f"{BASE_PATH}repository/{key}")

    def load_from_scp(self, zip_data: Asset) -> None:
        with zipfile.ZipFile(BytesIO(self._load_data(zip_data))) as zf:
            files_by_dir = self._group_zip_entries_by_directory(zf.filelist)
            self._process_zip_directories(zf, files_by_dir)

    def load_from_source(self, path: PathLike | str) -> None:
        root_path = Path(path)

        for category_dir in root_path.iterdir():
            if not category_dir.is_dir():
                continue

            category_name = category_dir.name
            if not self._is_valid_category(category_name):
                continue

            for item_dir in category_dir.iterdir():
                if not item_dir.is_dir():
                    continue

                item_json_path = item_dir / "item.json"
                if not item_json_path.exists():
                    continue

                try:
                    item_data = json.loads(item_json_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue

                item_data = self._localize_item(item_data)
                item_data["name"] = item_dir.name

                for resource_path in item_dir.iterdir():
                    if resource_path.name == "item.json":
                        continue

                    try:
                        resource_data = resource_path.read_bytes()

                        if resource_path.suffix.lower() in {".json", ".bin"}:
                            resource_data = gzip.compress(resource_data)

                        srl = self.add_asset(resource_data)
                        item_data[resource_path.stem] = srl

                    except Exception as e:
                        print(f"Error processing resource {resource_path}: {e}")
                        continue

                self.add_item(category_name, item_dir.name, item_data)

    @classmethod
    def _localize_item(cls, item: dict[str, Any]) -> dict[str, Any]:
        localized_item = item.copy()
        for key in LOCALIZED_KEYS:
            if key not in localized_item:
                continue
            localized_item[key] = cls._localize_text(localized_item[key])
        if "tags" in localized_item:
            localized_item["tags"] = [
                {**tag, "title": cls._localize_text(tag["title"])} for tag in localized_item["tags"]
            ]
        localized_item.pop("meta", None)
        return localized_item

    @staticmethod
    def _localize_text(text: str | dict[str, str]) -> str:
        match text:
            case str():
                return text
            case {"en": localized_text}:
                return localized_text
            case {**other_languages} if other_languages:
                return text[min(other_languages)]
            case _:
                return ""

    def _group_zip_entries_by_directory(self, file_list: list[zipfile.ZipInfo]) -> dict[str, list[zipfile.ZipInfo]]:
        files_by_dir: dict[str, list[zipfile.ZipInfo]] = {}

        for zip_entry in file_list:
            if self._should_skip_zip_entry(zip_entry):
                continue

            path_parts = Path(zip_entry.filename).parts
            if path_parts[0] == "sonolus":
                path_parts = path_parts[1:]

            if not path_parts:
                continue

            dir_name = path_parts[0]
            files_by_dir.setdefault(dir_name, []).append(zip_entry)

        return files_by_dir

    def _should_skip_zip_entry(self, zip_entry: zipfile.ZipInfo) -> bool:
        path = Path(zip_entry.filename)
        if path.parts[0] == "sonolus":
            path = Path(*path.parts[1:])
        return zip_entry.filename.endswith("/") or len(path.parts) < 2 or path.name.lower() in RESERVED_FILENAMES

    def _process_zip_directories(self, zf: zipfile.ZipFile, files_by_dir: dict[str, list[zipfile.ZipInfo]]) -> None:
        for dir_name, zip_entries in files_by_dir.items():
            if dir_name == "repository":
                self._add_repository_items(zf, zip_entries)
            elif self._is_valid_category(dir_name):
                self.categories.setdefault(dir_name, {})
                self._extract_category_items(zf, dir_name, zip_entries)

    def _add_repository_items(self, zf: zipfile.ZipFile, zip_entries: list[zipfile.ZipInfo]) -> None:
        for zip_entry in zip_entries:
            self.repository[Path(zip_entry.filename).name] = zf.read(zip_entry)

    def _is_valid_category(self, category: str) -> TypeGuard[Category]:
        return category in CATEGORY_NAMES

    def _extract_category_items(
        self, zf: zipfile.ZipFile, dir_name: Category, zip_entries: list[zipfile.ZipInfo]
    ) -> None:
        for zip_entry in zip_entries:
            try:
                item_details = json.loads(zf.read(zip_entry).decode("utf-8"))
            except json.JSONDecodeError:
                continue

            path = Path(zip_entry.filename)
            if path.parts[0] == "sonolus":
                path = Path(*path.parts[1:])
            item_name = path.stem

            if self._is_valid_category(dir_name):
                self.categories[dir_name][item_name] = item_details

    def write(self, path: Asset) -> None:
        self.link()
        base_dir = self._create_base_directory(path)
        self._write_main_info(base_dir)
        self._write_category_items(base_dir)
        self._write_repository_items(base_dir)

    def link(self):
        for level_details in self.categories.get("levels", {}).values():
            level = level_details["item"]
            if isinstance(level["engine"], str):
                level["engine"] = self.get_item("engines", level["engine"])
            for key, category in (
                ("useSkin", "skins"),
                ("useBackground", "backgrounds"),
                ("useEffect", "effects"),
                ("useParticle", "particles"),
            ):
                use_item = level[key]
                if "item" not in use_item:
                    continue
                use_item["item"] = self.get_item(category, use_item["item"])

    def _create_base_directory(self, path: Asset) -> Path:
        base_dir = Path(path) / BASE_PATH.strip("/")
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir

    def _write_main_info(self, base_dir: Path) -> None:
        sorted_categories = sorted(self.categories.keys(), key=lambda c: CATEGORY_SORT_ORDER.get(c, 100))
        info = {
            "title": self.name,
            "buttons": [{"type": SINGULAR_CATEGORY_NAMES[category]} for category in sorted_categories],
            "configuration": {"options": []},
        }
        self._write_json(base_dir / "info", info)

    def _write_category_items(self, base_dir: Path) -> None:
        for category, items in self.categories.items():
            if not items:
                continue
            category_dir = self._create_category_directory(base_dir, category)
            self._write_category_structure(category_dir, category, items)

    def _create_category_directory(self, base_dir: Path, category: Category) -> Path:
        category_dir = base_dir / category
        category_dir.mkdir(exist_ok=True)
        return category_dir

    def _write_category_structure(self, category_dir: Path, category: Category, items: dict[str, Any]) -> None:
        self._write_json(
            category_dir / "info",
            {
                "sections": [
                    {
                        "itemType": SINGULAR_CATEGORY_NAMES[category],
                        "title": "Items",
                        "items": [item_details["item"] for item_details in items.values()],
                    }
                ]
            },
        )

        category_list = {"pageCount": 1, "items": [item_details["item"] for item_details in items.values()]}
        self._write_json(category_dir / "list", category_list)

        for item_name, item_details in items.items():
            self._write_json(category_dir / item_name, item_details)

    def _write_repository_items(self, base_dir: Path) -> None:
        repo_dir = base_dir / "repository"
        repo_dir.mkdir(exist_ok=True)

        for key, data in self.repository.items():
            (repo_dir / key).write_bytes(data)

    @staticmethod
    def _write_json(path: Path, content: Any) -> None:
        path.write_text(json.dumps(content), encoding="utf-8")

    def update(self, other: Collection) -> None:
        self.repository.update(other.repository)
        for category, items in other.categories.items():
            self.categories.setdefault(category, {}).update(items)


class Srl(TypedDict):
    hash: str
    url: str


def load_asset(value: Asset) -> bytes:
    match value:
        case str() if value.startswith(("http://", "https://")):
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
            }
            request = urllib.request.Request(value, headers=headers)
            with urllib.request.urlopen(request) as response:
                return response.read()
        case PathLike():
            return Path(value).read_bytes()
        case bytes():
            return value
        case _:
            raise TypeError("value must be a URL, a path, or bytes")
