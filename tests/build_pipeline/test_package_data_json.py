"""Tests that package_data emits conformant JSON: no Infinity/-Infinity/NaN tokens, ever.

Those three tokens are a Python extension that the JSON grammar does not have, so a payload containing them is
rejected by spec-conformant parsers even though Python reads it back happily. That is why unpackage_data cannot
serve as the check here: it is json.loads, which accepts the same extension json.dumps emits, so the pair is a
fixed point on exactly the payloads a conformant consumer rejects. These tests parse with parse_constant instead,
which is the hook the stdlib calls for each of the three tokens.
"""

import gzip
import json
import math

import pytest

from sonolus.build.engine import package_data


def _reject_constant(name: str):
    raise AssertionError(f"non-conformant JSON token: {name}")


def _strict_loads(data: bytes):
    return json.loads(gzip.decompress(data), parse_constant=_reject_constant)


def test_strict_parse_helper_rejects_the_python_extension_tokens():
    # Sensitivity check for the two tests below: without it, a strict parse that silently accepted Infinity would
    # make them pass for the wrong reason.
    for text in (b'{"a":Infinity}', b'{"a":-Infinity}', b'{"a":NaN}'):
        with pytest.raises(AssertionError, match="non-conformant JSON token"):
            json.loads(text, parse_constant=_reject_constant)


def test_packaged_payload_parses_under_a_strict_parser():
    value = {
        "bgmOffset": -0.5,
        "entities": [
            {"archetype": "Note", "data": [{"name": "beat", "value": 1.25}, {"name": "lane", "value": -3.0}]},
            {"archetype": "Note", "data": [{"name": "beat", "value": 1e30}, {"name": "lane", "value": 5e-324}]},
        ],
    }

    assert _strict_loads(package_data(value)) == value


@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan], ids=["inf", "-inf", "nan"])
@pytest.mark.parametrize(
    "wrap",
    [
        lambda v: v,
        lambda v: [v],
        lambda v: {"bgmOffset": v},
        lambda v: {"entities": [{"data": [{"name": "beat", "value": v}]}]},
    ],
    ids=["bare", "in_list", "in_dict", "nested"],
)
def test_package_data_rejects_non_finite_values(wrap, bad):
    with pytest.raises(ValueError, match="Out of range float values are not JSON compliant"):
        package_data(wrap(bad))


@pytest.mark.parametrize("sentinel", [math.inf, -math.inf, math.nan], ids=["inf", "-inf", "nan"])
@pytest.mark.parametrize("shape", ["elif", "dict"], ids=["elif", "dict"])
def test_non_finite_switch_labels_package_to_conformant_json(shape, sentinel):
    # A comparison chain or dict lookup keyed by a non-finite constant lowers to a switch whose case labels
    # must reach the payload as EngineRom reads, never as bare Infinity/-Infinity/NaN leaves. The elif shape
    # only produces switch labels once rewrite_switch has hoisted the comparisons (standard passes); the dict
    # shape labels its switch edges at trace time, so it covers emission with no optimizer involvement.
    from sonolus.build.engine import package_engine
    from sonolus.script.archetype import PlayArchetype, entity_memory
    from sonolus.script.engine import EngineData, PlayMode
    from sonolus.script.project import BuildConfig

    if shape == "elif":

        class Note(PlayArchetype):
            v: float = entity_memory()
            out: float = entity_memory()

            def update_sequential(self):
                x = self.v
                if x == sentinel:
                    self.out = 11
                elif x == 2:
                    self.out = 22
                elif x == 3:
                    self.out = 33
                elif x == 4:
                    self.out = 44
                else:
                    self.out = 55

        passes = BuildConfig.STANDARD_PASSES
    else:

        class Note(PlayArchetype):
            v: float = entity_memory()
            out: float = entity_memory()

            def update_sequential(self):
                table = {sentinel: 11.0, 2.0: 22.0, 3.0: 33.0}
                self.out = table[self.v]

        passes = BuildConfig.MINIMAL_PASSES

    package = package_engine(
        EngineData(play=PlayMode(archetypes=[Note])),
        BuildConfig(passes=passes, build_watch=False, build_preview=False, build_tutorial=False),
    )
    assert _strict_loads(package.play_data)
