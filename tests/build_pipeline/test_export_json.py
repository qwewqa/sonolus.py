"""Tests that every item.json writer refuses a non-finite float rather than emitting Infinity or NaN.

Those three tokens are a Python extension to JSON: json.dumps writes them by default and json.loads reads them
back, so the pair agrees on a dialect that the Sonolus client, and any other conformant parser, rejects. The
package_data choke point already refuses them (tests/build_pipeline/test_package_data_json.py); these are the
item-metadata writers on either side of it, which serialize with the stdlib defaults.
"""

import json
import math

import pytest

from sonolus.build.collection import Collection
from sonolus.script.engine import Engine, EngineData
from sonolus.script.level import Level, LevelData


def _reject_constant(name: str):
    raise AssertionError(f"non-conformant JSON token: {name}")


def make_level(meta) -> Level:
    return Level(name="lvl", data=LevelData(bgm_offset=0.0, entities=[]), meta=meta)


def make_engine(meta) -> Engine:
    return Engine(name="eng", data=EngineData(), skin="s1", background="b1", effect="e1", particle="p1", meta=meta)


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan], ids=["inf", "-inf", "nan"])
def test_an_exported_level_refuses_a_non_finite_meta_value(value, tmp_path):
    with pytest.raises(ValueError, match="not JSON compliant"):
        make_level({"x": value}).export("eng").write_to_dir(tmp_path / "level")


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan], ids=["inf", "-inf", "nan"])
def test_an_exported_engine_refuses_a_non_finite_meta_value(value, tmp_path):
    with pytest.raises(ValueError, match="not JSON compliant"):
        make_engine({"x": value}).export().write_to_dir(tmp_path / "engine")


def test_an_exported_level_writes_a_finite_meta_value(tmp_path):
    make_level({"x": 1.5}).export("eng").write_to_dir(tmp_path / "level")

    item = json.loads((tmp_path / "level" / "item.json").read_text(encoding="utf-8"), parse_constant=_reject_constant)
    assert item["meta"] == {"x": 1.5}


def test_a_collection_refuses_to_write_a_non_finite_item_value(tmp_path):
    # The load side accepts the same extension, so a third-party pack under resources/ can carry an Infinity all
    # the way here; the message names the file the dev server would otherwise serve.
    collection = Collection()
    collection.add_item("skins", "packskin", {"name": "packskin", "version": math.inf})

    # The category index carries the same item and is written before the item file, so it is the write that
    # refuses first.
    with pytest.raises(ValueError, match=r"Cannot write .*skins[/\\]info: Out of range float values"):
        collection.write(tmp_path)


def test_a_collection_writes_finite_item_values(tmp_path):
    collection = Collection()
    collection.add_item("skins", "packskin", {"name": "packskin", "version": 5})

    collection.write(tmp_path)

    item = json.loads(
        (tmp_path / "sonolus" / "skins" / "packskin").read_text(encoding="utf-8"),
        parse_constant=_reject_constant,
    )
    assert item["item"]["version"] == 5
