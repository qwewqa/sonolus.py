"""Tests for Engine.export: which fields the exported item must carry, and what a full export produces.

An exported engine is a directory a packer reads on its own, with no project around it to consult. The four
resource references are the only required fields of the item with no default, so they are the ones an author has
to supply for the result to be complete.
"""

import json

import pytest

from sonolus.script.engine import Engine, EngineData

FULL_REFERENCES = {"skin": "s1", "background": "b1", "effect": "e1", "particle": "p1"}


def make_engine(**references) -> Engine:
    return Engine(name="exported", data=EngineData(), **references)


@pytest.mark.parametrize("missing", sorted(FULL_REFERENCES))
def test_export_rejects_an_engine_with_an_unset_resource_reference(missing):
    references = {key: value for key, value in FULL_REFERENCES.items() if key != missing}

    with pytest.raises(ValueError, match=r"Engine\.export requires") as exc_info:
        make_engine(**references).export()

    text = str(exc_info.value)
    assert missing in text
    for present in references:
        assert present not in text


def test_export_names_every_unset_resource_reference():
    with pytest.raises(ValueError, match=r"Engine\.export requires") as exc_info:
        make_engine().export()

    text = str(exc_info.value)
    for key in FULL_REFERENCES:
        assert key in text


def test_export_of_a_fully_referenced_engine_carries_the_references():
    exported = make_engine(**FULL_REFERENCES).export()

    assert {key: exported.item[key] for key in FULL_REFERENCES} == FULL_REFERENCES
    assert None not in exported.item.values()


def test_export_writes_an_item_and_its_siblings(tmp_path):
    exported = make_engine(**FULL_REFERENCES).export()

    exported.write_to_dir(tmp_path / "engine")

    # The names write_to_dir documents: seven unconditional ones, plus rom when the engine has one.
    expected = {
        "item.json",
        "thumbnail",
        "playData",
        "watchData",
        "previewData",
        "tutorialData",
        "configuration",
    }
    if exported.rom is not None:
        expected.add("rom")

    assert {path.name for path in (tmp_path / "engine").iterdir()} == expected
    assert json.loads((tmp_path / "engine" / "item.json").read_text(encoding="utf-8"))["skin"] == "s1"
