"""Unit tests for the engine build payload assembly (PORT.md task T4.1).

Asserts that ``sonolus.build.payload.assemble_engine_payload`` produces the
rust/PAYLOAD.md schema-v1 payload for ``test_projects/pydori``: the work-unit
lists match what the legacy build compiles (derived from the same project
introspection ``compile_mode`` uses, not hardcoded), the encoded CFG bytes
round-trip through the Rust decoder, the metadata JSON matches the legacy-built
engine data on every overlapping field (including key order, which is
meaning-bearing), the ROM matches, and dedup-key collisions imply identical
bytes. The legacy build path stays untouched and is used here as the oracle.
"""

import gzip
import json
import struct

import pytest

from sonolus.build.engine import package_engine, unpackage_data
from sonolus.build.payload import (
    LEVELS,
    PAYLOAD_SCHEMA_VERSION,
    assemble_engine_payload,
    dedup_key,
    level_from_passes,
)
from sonolus.script.internal.callbacks import (
    navigate_callback,
    preprocess_callback,
    update_callback,
    update_spawn_callback,
)
from sonolus.script.project import BuildConfig
from tests.regressions import pydori_project

MODE_KEYS = ("play", "watch", "preview", "tutorial")


def _f64_bits(values):
    return struct.pack(f"<{len(values)}d", *values)


# Keys of an archetype entry that are not callback slots (PAYLOAD.md §3.2).
ARCHETYPE_STATIC_KEYS = {"name", "hasInput", "imports", "exports"}


@pytest.fixture(scope="module")
def payload():
    return assemble_engine_payload(pydori_project.engine.data, BuildConfig(), level="standard")


@pytest.fixture(scope="module")
def legacy():
    # FAST_PASSES keeps the oracle build quick; every field the payload carries
    # (and the ROM) is independent of the optimization passes.
    return package_engine(pydori_project.engine.data, BuildConfig(passes=BuildConfig.FAST_PASSES))


@pytest.fixture(scope="module")
def legacy_mode_json(legacy):
    return {
        "play": unpackage_data(legacy.play_data),
        "watch": unpackage_data(legacy.watch_data),
        "preview": unpackage_data(legacy.preview_data),
        "tutorial": unpackage_data(legacy.tutorial_data),
    }


def _expected_mode_units():
    """Derives the per-mode (callback, archetype index, order) work-unit lists.

    Uses the exact ``compile_mode`` introspection the legacy build uses (base
    archetype dedup via ``_derived_base_`` in first-encounter order, the
    supported/non-default callback filter, callback order via
    ``_callback_order_``), followed by the mode's global callbacks.
    """
    engine = pydori_project.engine.data

    def mode_units(archetypes, global_callbacks):
        units = []
        base_archetypes = []
        first_instance_index = {}
        for i, a in enumerate(archetypes):
            base = getattr(a, "_derived_base_", a)
            if base not in first_instance_index:
                first_instance_index[base] = i
                base_archetypes.append(base)
        for base in base_archetypes:
            base._init_fields()
            for cb_name, cb_info in base._supported_callbacks_.items():
                if cb_name not in base._callbacks_ or base._callbacks_[cb_name] in base._default_callbacks_:
                    continue
                cb_order = getattr(base._callbacks_[cb_name], "_callback_order_", 0)
                units.append((cb_info.name, first_instance_index[base], cb_order))
        for cb_info, _cb in global_callbacks:
            units.append((cb_info.name, None, 0))
        return units

    return {
        "play": mode_units(engine.play.archetypes, []),
        "watch": mode_units(engine.watch.archetypes, [(update_spawn_callback, engine.watch.update_spawn)]),
        "preview": mode_units(engine.preview.archetypes, []),
        "tutorial": mode_units(
            [],
            [
                (preprocess_callback, engine.tutorial.preprocess),
                (navigate_callback, engine.tutorial.navigate),
                (update_callback, engine.tutorial.update),
            ],
        ),
    }


def test_payload_schema_version_is_pinned(payload):
    assert PAYLOAD_SCHEMA_VERSION == 1
    assert payload["schema"] == 1
    assert list(payload.keys()) == ["schema", "level", "configuration", "rom", "modes"]
    assert payload["level"] == "standard"
    assert list(payload["modes"].keys()) == list(MODE_KEYS)
    for mode_key in MODE_KEYS:
        mode = payload["modes"][mode_key]
        assert list(mode.keys()) == ["metadata", "units"]
        assert isinstance(mode["metadata"], str)
        for unit in mode["units"]:
            assert list(unit.keys()) == ["callback", "archetype", "order", "cfg"]
            assert isinstance(unit["callback"], str)
            assert unit["archetype"] is None or isinstance(unit["archetype"], int)
            assert isinstance(unit["order"], int)
            assert isinstance(unit["cfg"], bytes)


def test_work_units_match_legacy_compile_enumeration(payload):
    expected = _expected_mode_units()
    for mode_key in MODE_KEYS:
        actual = [(u["callback"], u["archetype"], u["order"]) for u in payload["modes"][mode_key]["units"]]
        assert actual == expected[mode_key], f"work units differ for mode {mode_key}"
    # The enumeration must be non-trivial for the fixture project.
    assert sum(len(units) for units in expected.values()) > 0


def test_encoded_cfgs_decode_via_rust(payload):
    sonolus_backend = pytest.importorskip("sonolus_backend")
    count = 0
    for mode_key in MODE_KEYS:
        for i, unit in enumerate(payload["modes"][mode_key]["units"]):
            dump = sonolus_backend.decode_cfg_canonical_dump(unit["cfg"])
            assert dump.startswith("cfg-canonical v1\n"), f"{mode_key} unit {i} produced an unexpected dump header"
            count += 1
    assert count > 0


def test_metadata_matches_legacy_mode_data(payload, legacy_mode_json):
    for mode_key in MODE_KEYS:
        mode = payload["modes"][mode_key]
        meta = json.loads(mode["metadata"])
        units = mode["units"]
        legacy_json = legacy_mode_json[mode_key]

        # Key order is meaning-bearing (PAYLOAD.md §3) and must mirror legacy.
        assert list(meta.keys()) == list(legacy_json.keys()), f"top-level key order differs for {mode_key}"

        global_keys = {u["callback"] for u in units if u["archetype"] is None}
        for key, value in meta.items():
            if key == "nodes":
                # Placeholder in the payload; generated by T4.2 in the legacy build.
                assert value is None
                assert isinstance(legacy_json[key], list)
            elif key == "archetypes":
                continue  # Checked below.
            elif key in global_keys:
                # Global callback slot: unit id here, node index in legacy.
                unit = units[value]
                assert unit["callback"] == key
                assert unit["archetype"] is None
                assert isinstance(legacy_json[key], int)
            else:
                # Resource/tail fields must match the legacy build exactly.
                assert value == legacy_json[key], f"{mode_key}.{key} differs from legacy"

        payload_archetypes = meta["archetypes"]
        legacy_archetypes = legacy_json["archetypes"]
        assert len(payload_archetypes) == len(legacy_archetypes)
        first_entry_referencing_unit = {}
        for i, (pe, le) in enumerate(zip(payload_archetypes, legacy_archetypes, strict=True)):
            assert list(pe.keys()) == list(le.keys()), f"{mode_key} archetype {i} key order differs"
            for key in pe:
                if key in ARCHETYPE_STATIC_KEYS:
                    assert pe[key] == le[key], f"{mode_key} archetype {i} field {key} differs"
                else:
                    # Callback slot: same shape and order value; index is a unit
                    # id here and a node index in legacy.
                    assert list(pe[key].keys()) == ["index", "order"] == list(le[key].keys())
                    assert pe[key]["order"] == le[key]["order"], f"{mode_key} archetype {i} {key} order differs"
                    assert isinstance(le[key]["index"], int)
                    unit = units[pe[key]["index"]]
                    assert unit["callback"] == key
                    assert unit["order"] == pe[key]["order"]
                    first_entry_referencing_unit.setdefault(pe[key]["index"], i)
        # A unit's archetype index is the first entry that references it
        # (PAYLOAD.md §2); derived archetypes share their base's units.
        for unit_id, entry_index in first_entry_referencing_unit.items():
            assert units[unit_id]["archetype"] == entry_index

        # Every unit must be wired into the metadata exactly where it claims.
        referenced = set(first_entry_referencing_unit) | {meta[key] for key in global_keys}
        assert referenced == set(range(len(units))), f"unreferenced or dangling units in {mode_key}"


def test_derived_archetypes_share_units(payload):
    # pydori derives archetypes in watch and preview; sharing must show up as
    # multiple archetype entries referencing the same unit ids.
    shared = False
    for mode_key in MODE_KEYS:
        meta = json.loads(payload["modes"][mode_key]["metadata"])
        references = [
            entry[key]["index"] for entry in meta["archetypes"] for key in entry if key not in ARCHETYPE_STATIC_KEYS
        ]
        if len(references) > len(set(references)):
            shared = True
    assert shared, "expected at least one mode with base-shared work units"


def test_configuration_matches_legacy(payload, legacy):
    assert payload["configuration"].encode("utf-8") == gzip.decompress(legacy.configuration)


def test_rom_matches_legacy(payload, legacy):
    rom = payload["rom"]
    assert rom, "payload ROM must never be empty"
    assert all(isinstance(v, float) for v in rom)
    packed = b"".join(struct.pack("<f", v) for v in rom)
    assert packed == gzip.decompress(legacy.rom)


def test_dedup_key_collisions_are_identical_bytes(payload):
    by_key = {}
    for mode_key in MODE_KEYS:
        for unit in payload["modes"][mode_key]["units"]:
            by_key.setdefault(dedup_key(unit["cfg"]), set()).add(unit["cfg"])
    for key, cfgs in by_key.items():
        assert len(cfgs) == 1, f"dedup key {key.hex()} maps to {len(cfgs)} distinct byte strings"
    # And the converse: identical bytes always produce identical keys.
    some_cfg = next(iter(next(iter(by_key.values()))))
    assert dedup_key(some_cfg) == dedup_key(bytes(some_cfg))
    assert dedup_key(some_cfg) != dedup_key(some_cfg + b"\x00")


def test_assembly_is_deterministic(payload):
    second = assemble_engine_payload(pydori_project.engine.data, BuildConfig(), level="standard")
    assert second["schema"] == payload["schema"]
    assert second["level"] == payload["level"]
    assert second["configuration"] == payload["configuration"]
    # Bit-exact f64 comparison (the ROM starts with NaN, which is not self-equal).
    assert _f64_bits(second["rom"]) == _f64_bits(payload["rom"])
    assert list(second["modes"].keys()) == list(payload["modes"].keys())
    for mode_key in MODE_KEYS:
        first_mode = payload["modes"][mode_key]
        second_mode = second["modes"][mode_key]
        assert second_mode["metadata"] == first_mode["metadata"], f"metadata not deterministic for {mode_key}"
        assert len(second_mode["units"]) == len(first_mode["units"])
        for i, (a, b) in enumerate(zip(first_mode["units"], second_mode["units"], strict=True)):
            assert a == b, f"unit {i} of {mode_key} not deterministic"


def test_empty_modes_match_legacy():
    config = BuildConfig(
        passes=BuildConfig.FAST_PASSES,
        build_play=False,
        build_watch=False,
        build_preview=False,
        build_tutorial=False,
    )
    empty_payload = assemble_engine_payload(pydori_project.engine.data, config, level="standard")
    legacy = package_engine(pydori_project.engine.data, config)
    legacy_json = {
        "play": unpackage_data(legacy.play_data),
        "watch": unpackage_data(legacy.watch_data),
        "preview": unpackage_data(legacy.preview_data),
        "tutorial": unpackage_data(legacy.tutorial_data),
    }
    for mode_key in MODE_KEYS:
        mode = empty_payload["modes"][mode_key]
        meta = json.loads(mode["metadata"])
        assert list(meta.keys()) == list(legacy_json[mode_key].keys()), f"key order differs for empty {mode_key}"
        assert meta["archetypes"] == [] == legacy_json[mode_key]["archetypes"]
    # Play and preview have no callbacks at all when empty.
    assert empty_payload["modes"]["play"]["units"] == []
    assert empty_payload["modes"]["preview"]["units"] == []
    # The watch/tutorial global callbacks are still traced (legacy parity).
    assert [u["callback"] for u in empty_payload["modes"]["watch"]["units"]] == ["updateSpawn"]
    assert [u["callback"] for u in empty_payload["modes"]["tutorial"]["units"]] == ["preprocess", "navigate", "update"]
    # The default callbacks trace to byte-identical CFGs, both within a mode and
    # across modes — a positive exercise of the D6 intra-call dedup identity:
    # equal bytes, equal dedup keys, one compilation in Rust (PAYLOAD.md §5).
    watch_cfg = empty_payload["modes"]["watch"]["units"][0]["cfg"]
    tutorial_cfgs = [u["cfg"] for u in empty_payload["modes"]["tutorial"]["units"]]
    assert all(cfg == watch_cfg for cfg in tutorial_cfgs)
    assert all(dedup_key(cfg) == dedup_key(watch_cfg) for cfg in tutorial_cfgs)
    # ROM and configuration still package identically.
    packed = b"".join(struct.pack("<f", v) for v in empty_payload["rom"])
    assert packed == gzip.decompress(legacy.rom)
    assert empty_payload["configuration"].encode("utf-8") == gzip.decompress(legacy.configuration)


def test_unknown_level_is_rejected():
    with pytest.raises(ValueError, match="Unknown optimization level"):
        assemble_engine_payload(pydori_project.engine.data, BuildConfig(), level="turbo")


def test_level_from_passes():
    assert level_from_passes(BuildConfig.MINIMAL_PASSES) == "minimal"
    assert level_from_passes(BuildConfig.FAST_PASSES) == "fast"
    assert level_from_passes(BuildConfig.STANDARD_PASSES) == "standard"
    assert level_from_passes(BuildConfig().passes) == "standard"
    assert set(LEVELS) == {"minimal", "fast", "standard"}
    with pytest.raises(ValueError, match="not representable"):
        level_from_passes(BuildConfig.STANDARD_PASSES[:-1])
