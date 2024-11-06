import gzip
import json
from pathlib import Path
from typing import Any

BLANK_PNG = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x01\x00\x00\x00\x007n\xf9$\x00\x00\x00\nIDATx\x01c`\x00\x00\x00\x02\x00\x01su\x01\x18\x00\x00\x00\x00IEND\xaeB`\x82'
BLANK_AUDIO = b'RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00'

type AnyResource = bytes | Path


def load_resource(value: Path | bytes, /) -> bytes:
    match value:
        case Path() as path if path.suffix == ".json":
            return gzip.compress(path.read_bytes())
        case Path() as path:
            return path.read_bytes()
        case bytes() as data:
            return data
        case _:
            raise TypeError("Invalid resource value")

def load_item(path: Path) -> Item:
    data = json.loads(load_resource(path / "item.json"))
    resources = {
        file.name: load_resource(file)
        for file in path.iterdir()
        if file.name != "item.json"
    }
    return Item(data, resources)


def load_items(path: Path) -> dict[str, Item]:
    return {
        item.name: load_item(item)
        for item in path.iterdir()
        if item.is_dir() and (item / "item.json").exists()
    }
