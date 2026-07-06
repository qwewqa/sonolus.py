"""Tests for sonolus.build.collection.Collection output writing."""

import json

from sonolus.build.collection import SINGULAR_CATEGORY_NAMES, Collection


def test_write_main_info_omits_empty_categories(tmp_path):
    c = Collection()
    c.categories["levels"] = {}  # empty
    c.categories["skins"] = {"s1": {"item": {}}}  # non-empty

    c._write_main_info(tmp_path)

    info = json.loads((tmp_path / "info").read_text(encoding="utf-8"))
    button_types = {b["type"] for b in info["buttons"]}
    assert SINGULAR_CATEGORY_NAMES["skins"] in button_types
    assert SINGULAR_CATEGORY_NAMES["levels"] not in button_types
