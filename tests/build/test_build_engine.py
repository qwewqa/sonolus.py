"""A/B tests for the Rust single-call engine build (PORT.md task T4.2).

Builds the pydori engine twice — through the frozen legacy path
(``package_engine``) and through the payload path
(``assemble_engine_payload`` -> ``sonolus_backend.build_engine``) — and checks
the rust/PAYLOAD.md §7 parity contract:

- ``EngineConfiguration`` and ``EngineRom``: decompressed bytes identical.
- Mode data: decompressed JSON structurally identical *except* the parts
  derived from compilation output (callback node-index values and the
  ``"nodes"`` array contents — the Rust optimizer is redesigned, decision D2),
  including key order, which is meaning-bearing.
- Per-callback differential interpretation: each work unit's node tree from
  both backends runs on the Rust interpreter under identical seeded memory and
  RNG, and must agree on result/error, debug log, and final memory.
- Determinism: two ``build_engine`` calls on one payload are byte-identical.

Both runtime-check variants are covered (``release`` and ``dev`` configs,
mirroring the dev server): 60 work units each, 120 distinct compilations
total — every callback the legacy build compiles (the 300-row metrics
enumeration counts derived-archetype instances separately; those share their
base's compilation in both backends, which the structural test pins via the
node-index sharing pattern).

Skipped when the ``sonolus_backend`` extension is not installed.
"""

import gzip
import json
import math
import struct
from types import SimpleNamespace

import pytest

sonolus_backend = pytest.importorskip("sonolus_backend")

from sonolus.build.engine import package_engine, unpackage_data
from sonolus.build.payload import assemble_engine_payload
from sonolus.script.internal.context import RuntimeChecks
from sonolus.script.project import BuildConfig
from tests.regressions import pydori_project

MODE_KEYS = ("play", "watch", "preview", "tutorial")

# Keys of an archetype entry that are not callback slots (PAYLOAD.md §3.2).
ARCHETYPE_STATIC_KEYS = {"name", "hasInput", "imports", "exports"}

#: (memory_seed, rng_seed) pairs tried in order when a run exceeds the eval
#: budget (the documented harness seeds from tools/metrics.py).
SEED_ATTEMPTS = (
    (0x5EED_0001, 0x0123_4567),
    (0x5EED_0002, 0x89AB_CDEF),
    (0x5EED_0003, 0x4242_4242),
)

#: Termination backstop; a budget cutoff is not a semantic fact.
EVAL_BUDGET = 200_000_000

#: Slot layouts are pipeline-specific; the temp runtime block is never compared.
TEMP_RUNTIME_BLOCK = 10000


def _build_configs():
    return {
        "release": BuildConfig(),
        "dev": BuildConfig(runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE),
    }


@pytest.fixture(scope="module", params=["release", "dev"])
def ab(request):
    """One A/B pair: the payload-path build and the legacy sequential build.

    Both run the published ``standard`` passes — the real shipped artifact on
    both sides.
    """
    config = _build_configs()[request.param]
    engine = pydori_project.engine.data
    payload = assemble_engine_payload(engine, config, level="standard")
    rust = sonolus_backend.build_engine(payload)
    legacy = package_engine(engine, config)
    rust_json = {mode: json.loads(gzip.decompress(rust[f"{mode}_data"])) for mode in MODE_KEYS}
    legacy_json = {
        "play": unpackage_data(legacy.play_data),
        "watch": unpackage_data(legacy.watch_data),
        "preview": unpackage_data(legacy.preview_data),
        "tutorial": unpackage_data(legacy.tutorial_data),
    }
    return SimpleNamespace(
        label=request.param,
        payload=payload,
        rust=rust,
        legacy=legacy,
        rust_json=rust_json,
        legacy_json=legacy_json,
    )


# ----------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------


def _values_match(a: float, b: float) -> bool:
    """Raw-bit equality, NaN-aware and zero-sign-tolerant (the documented
    legacy contract; mirrors diff.rs ``values_match``)."""
    if math.isnan(a) and math.isnan(b):
        return True
    if a == 0.0 and b == 0.0:
        return True
    return struct.pack("<d", a) == struct.pack("<d", b)


def _assert_strict_equal(a, b, where: str) -> None:
    """Deep equality that is type-sensitive (no int/float coercion) and
    key-order-sensitive for dicts."""
    stack = [(a, b, where)]
    while stack:
        x, y, at = stack.pop()
        assert type(x) is type(y), f"{at}: type {type(x).__name__} != {type(y).__name__}"
        if isinstance(x, dict):
            assert list(x) == list(y), f"{at}: key order differs"
            stack.extend((x[k], y[k], f"{at}.{k}") for k in x)
        elif isinstance(x, list):
            assert len(x) == len(y), f"{at}: length {len(x)} != {len(y)}"
            stack.extend((vx, vy, f"{at}[{i}]") for i, (vx, vy) in enumerate(zip(x, y, strict=True)))
        else:
            assert x == y, f"{at}: {x!r} != {y!r}"


def _build_tree_table(nodes: list) -> list:
    """Converts a flat mode node array into per-index nested ``(func, [args])``
    data accepted by ``sonolus_backend.EngineNodes``.

    Children always precede parents in the array (legacy ``_add`` and the Rust
    generator both append post-order), so one ascending pass suffices. Shared
    sub-trees become shared Python objects; the ``EngineNodes`` walk expands
    them per occurrence, which is exactly the tree the backend compiled.
    """
    table = []
    for i, node in enumerate(nodes):
        if "value" in node:
            table.append(node["value"])
        else:
            args = node["args"]
            assert all(a < i for a in args), f"node {i} has a non-preceding argument"
            table.append((node["func"], [table[a] for a in args]))
    return table


def _unit_slot_paths(payload_mode) -> dict[int, tuple]:
    """Maps each unit id to the metadata path of (one of) its callback slots.

    Global units map to ``(callback,)``; archetype units to
    ``("archetypes", entry_index, callback, "index")`` of the first entry
    referencing them. The same path resolves the node index in both backends'
    mode JSON.
    """
    meta = json.loads(payload_mode["metadata"])
    units = payload_mode["units"]
    paths: dict[int, tuple] = {}
    for i, unit in enumerate(units):
        if unit["archetype"] is None:
            paths[i] = (unit["callback"],)
    for entry_index, entry in enumerate(meta["archetypes"]):
        for key, value in entry.items():
            if key in ARCHETYPE_STATIC_KEYS:
                continue
            paths.setdefault(value["index"], ("archetypes", entry_index, key, "index"))
    assert set(paths) == set(range(len(units))), "every unit must be wired into the metadata"
    return paths


def _resolve(doc, path):
    for part in path:
        doc = doc[part]
    return doc


def _run_tree(tree_nodes, memory, rng_seed: int):
    """Runs one tree under seeded memory; returns ``(outcome, log, blocks)``
    or None when the eval budget was exceeded."""
    interp = sonolus_backend.Interpreter(seed=rng_seed)
    interp.set_eval_budget(EVAL_BUDGET)
    for block, values in memory:
        interp.set_block(block, values)
    try:
        result = interp.run(tree_nodes)
        outcome = ("ok", result)
    except RuntimeError as e:
        if str(e).startswith("eval budget exceeded"):
            return None
        outcome = ("error", type(e).__name__, str(e))
    except (AssertionError, ZeroDivisionError, ValueError, OverflowError, IndexError, NotImplementedError) as e:
        outcome = ("error", type(e).__name__, str(e))
    blocks = {bid: interp.get_block(bid) for bid in interp.block_ids() if bid != TEMP_RUNTIME_BLOCK}
    return outcome, list(interp.log), blocks


def _assert_same_observation(rust_obs, legacy_obs, where: str) -> None:
    rust_outcome, rust_log, rust_blocks = rust_obs
    legacy_outcome, legacy_log, legacy_blocks = legacy_obs
    # Result / error parity (NaN-aware result equality; exact error type+message).
    assert rust_outcome[0] == legacy_outcome[0], f"{where}: {rust_outcome} != {legacy_outcome}"
    if rust_outcome[0] == "ok":
        assert _values_match(rust_outcome[1], legacy_outcome[1]), (
            f"{where}: result {rust_outcome[1]!r} != {legacy_outcome[1]!r}"
        )
    else:
        assert rust_outcome == legacy_outcome, f"{where}: {rust_outcome} != {legacy_outcome}"
    # Debug log parity.
    assert len(rust_log) == len(legacy_log), f"{where}: log length {len(rust_log)} != {len(legacy_log)}"
    for i, (a, b) in enumerate(zip(rust_log, legacy_log, strict=True)):
        assert _values_match(a, b), f"{where}: log[{i}] {a!r} != {b!r}"
    # Final-memory parity over every runtime block except the temp block.
    # Blocks are extended on access with -1.0 fill, and an optimizer may
    # legitimately remove a dead read, so missing cells compare as -1.0.
    for bid in sorted(set(rust_blocks) | set(legacy_blocks)):
        a = rust_blocks.get(bid, [])
        b = legacy_blocks.get(bid, [])
        length = max(len(a), len(b))
        for i in range(length):
            av = a[i] if i < len(a) else -1.0
            bv = b[i] if i < len(b) else -1.0
            assert _values_match(av, bv), f"{where}: block {bid}[{i}] {av!r} != {bv!r}"


# ----------------------------------------------------------------------------------
# PAYLOAD.md §7 parity contract
# ----------------------------------------------------------------------------------


def test_unit_coverage(ab):
    counts = {mode: len(ab.payload["modes"][mode]["units"]) for mode in MODE_KEYS}
    # The full pydori build: every supported, overridden callback of every
    # base archetype plus the mode globals (pinned; update if pydori grows).
    assert counts == {"play": 23, "watch": 24, "preview": 10, "tutorial": 3}


def test_configuration_and_rom_byte_identical(ab):
    assert gzip.decompress(ab.rust["configuration"]) == gzip.decompress(ab.legacy.configuration)
    assert gzip.decompress(ab.rust["rom"]) == gzip.decompress(ab.legacy.rom)


def test_mode_data_structurally_equal(ab):
    for mode in MODE_KEYS:
        rust_doc = ab.rust_json[mode]
        legacy_doc = ab.legacy_json[mode]
        units = ab.payload["modes"][mode]["units"]
        global_keys = {u["callback"] for u in units if u["archetype"] is None}

        # Key order is meaning-bearing (PAYLOAD.md §3).
        assert list(rust_doc) == list(legacy_doc), f"{mode}: top-level key order differs"

        rust_slots: list[int] = []
        legacy_slots: list[int] = []
        for key in rust_doc:
            rv, lv = rust_doc[key], legacy_doc[key]
            if key == "nodes":
                # Contents legitimately differ (redesigned optimizer, D2).
                assert isinstance(rv, list) and isinstance(lv, list)
                if units:
                    assert rv, f"{mode}: rust node array is empty"
            elif key in global_keys:
                assert isinstance(rv, int) and isinstance(lv, int)
                assert 0 <= rv < len(rust_doc["nodes"])
                assert 0 <= lv < len(legacy_doc["nodes"])
                rust_slots.append(rv)
                legacy_slots.append(lv)
            elif key == "archetypes":
                assert len(rv) == len(lv), f"{mode}: archetype count differs"
                for i, (re_, le_) in enumerate(zip(rv, lv, strict=True)):
                    assert list(re_) == list(le_), f"{mode} archetype {i}: key order differs"
                    for k in re_:
                        if k in ARCHETYPE_STATIC_KEYS:
                            _assert_strict_equal(re_[k], le_[k], f"{mode} archetype {i}.{k}")
                        else:
                            assert list(re_[k]) == ["index", "order"] == list(le_[k])
                            _assert_strict_equal(re_[k]["order"], le_[k]["order"], f"{mode} archetype {i}.{k}.order")
                            assert isinstance(re_[k]["index"], int) and isinstance(le_[k]["index"], int)
                            assert 0 <= re_[k]["index"] < len(rust_doc["nodes"])
                            assert 0 <= le_[k]["index"] < len(legacy_doc["nodes"])
                            rust_slots.append(re_[k]["index"])
                            legacy_slots.append(le_[k]["index"])
            else:
                # Resource/tail fields must match exactly (type-sensitive).
                _assert_strict_equal(rv, lv, f"{mode}.{key}")

        # The sharing pattern of node indices must match: two slots reference
        # the same node in rust iff they do in legacy (derived archetypes and
        # deduplicated callbacks share identically in both backends).
        assert len(rust_slots) == len(legacy_slots)
        pattern = [
            (a == b) == (c == d)
            for i, (a, c) in enumerate(zip(rust_slots, legacy_slots, strict=True))
            for (b, d) in zip(rust_slots[i + 1 :], legacy_slots[i + 1 :], strict=True)
        ]
        assert all(pattern), f"{mode}: node-index sharing pattern differs from legacy"


def test_per_callback_differential(ab):
    total = 0
    for mode in MODE_KEYS:
        payload_mode = ab.payload["modes"][mode]
        units = payload_mode["units"]
        if not units:
            continue
        paths = _unit_slot_paths(payload_mode)
        rust_table = _build_tree_table(ab.rust_json[mode]["nodes"])
        legacy_table = _build_tree_table(ab.legacy_json[mode]["nodes"])
        for unit_id, unit in enumerate(units):
            where = f"{ab.label}/{mode} unit {unit_id} ({unit['callback']})"
            rust_index = _resolve(ab.rust_json[mode], paths[unit_id])
            legacy_index = _resolve(ab.legacy_json[mode], paths[unit_id])
            rust_tree = sonolus_backend.EngineNodes(rust_table[rust_index])
            legacy_tree = sonolus_backend.EngineNodes(legacy_table[legacy_index])
            for attempt, (memory_seed, rng_seed) in enumerate(SEED_ATTEMPTS):
                memory = sonolus_backend.seeded_memory(unit["cfg"], memory_seed)
                rust_obs = _run_tree(rust_tree, memory, rng_seed)
                legacy_obs = _run_tree(legacy_tree, memory, rng_seed)
                if rust_obs is None or legacy_obs is None:
                    # Budget cutoff is not a semantic fact; try the next seeds.
                    assert attempt < len(SEED_ATTEMPTS) - 1, f"{where}: every seed attempt exceeded the eval budget"
                    continue
                _assert_same_observation(rust_obs, legacy_obs, where)
                break
            total += 1
    assert total == 60, f"expected to compare all 60 work units, compared {total}"


def test_build_engine_is_deterministic(ab):
    again = sonolus_backend.build_engine(ab.payload)
    for key in ("configuration", "play_data", "watch_data", "preview_data", "tutorial_data", "rom"):
        assert again[key] == ab.rust[key], f"{key} not byte-identical across calls"


# ----------------------------------------------------------------------------------
# Cross-mode dedup on the empty-modes build (cheap; no module fixture)
# ----------------------------------------------------------------------------------


def test_empty_modes_build_matches_legacy_with_cross_mode_dedup():
    config = BuildConfig(
        build_play=False,
        build_watch=False,
        build_preview=False,
        build_tutorial=False,
    )
    engine = pydori_project.engine.data
    payload = assemble_engine_payload(engine, config, level="standard")
    # The four global callbacks (watch updateSpawn + tutorial x3) trace to
    # byte-identical CFGs — the cross-mode dedup case (PAYLOAD.md §5 step 2).
    cfgs = {u["cfg"] for m in MODE_KEYS for u in payload["modes"][m]["units"]}
    assert len(cfgs) == 1
    rust = sonolus_backend.build_engine(payload)
    legacy = package_engine(engine, config)
    assert gzip.decompress(rust["configuration"]) == gzip.decompress(legacy.configuration)
    assert gzip.decompress(rust["rom"]) == gzip.decompress(legacy.rom)
    watch = json.loads(gzip.decompress(rust["watch_data"]))
    tutorial = json.loads(gzip.decompress(rust["tutorial_data"]))
    legacy_watch = unpackage_data(legacy.watch_data)
    legacy_tutorial = unpackage_data(legacy.tutorial_data)
    assert list(watch) == list(legacy_watch)
    assert list(tutorial) == list(legacy_tutorial)
    # The shared compilation lands once per mode array; tutorial's three
    # globals share a single root, exactly like legacy's generator dedup.
    assert tutorial["preprocess"] == tutorial["navigate"] == tutorial["update"]
    assert (
        legacy_tutorial["preprocess"] == legacy_tutorial["navigate"] == legacy_tutorial["update"]
    )
    # Differential over the shared unit in both modes.
    for mode, doc, legacy_doc, key in (
        ("watch", watch, legacy_watch, "updateSpawn"),
        ("tutorial", tutorial, legacy_tutorial, "preprocess"),
    ):
        unit = payload["modes"][mode]["units"][0]
        rust_tree = sonolus_backend.EngineNodes(_build_tree_table(doc["nodes"])[doc[key]])
        legacy_tree = sonolus_backend.EngineNodes(_build_tree_table(legacy_doc["nodes"])[legacy_doc[key]])
        memory_seed, rng_seed = SEED_ATTEMPTS[0]
        memory = sonolus_backend.seeded_memory(unit["cfg"], memory_seed)
        rust_obs = _run_tree(rust_tree, memory, rng_seed)
        legacy_obs = _run_tree(legacy_tree, memory, rng_seed)
        assert rust_obs is not None and legacy_obs is not None
        _assert_same_observation(rust_obs, legacy_obs, f"empty/{mode}")


# ----------------------------------------------------------------------------------
# Error surface
# ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def empty_payload():
    config = BuildConfig(
        build_play=False,
        build_watch=False,
        build_preview=False,
        build_tutorial=False,
    )
    return assemble_engine_payload(pydori_project.engine.data, config, level="standard")


def test_rom_overflow_matches_struct_pack(empty_payload):
    # Legacy struct.pack("<f", v) raises OverflowError for finite |v| > f32
    # max; build_engine must error identically instead of saturating to inf.
    with pytest.raises(OverflowError, match="float too large to pack with f format"):
        struct.pack("<f", 1e39)
    payload = {**empty_payload, "rom": [1.0, 1e39]}
    with pytest.raises(OverflowError, match="float too large to pack with f format"):
        sonolus_backend.build_engine(payload)
    # NaN and infinities pack fine on both sides.
    payload = {**empty_payload, "rom": [math.nan, math.inf, -math.inf]}
    rom = gzip.decompress(sonolus_backend.build_engine(payload)["rom"])
    assert rom == struct.pack("<3f", math.nan, math.inf, -math.inf)


def test_malformed_payloads_are_rejected(empty_payload):
    with pytest.raises(ValueError, match="unsupported payload schema"):
        sonolus_backend.build_engine({**empty_payload, "schema": 2})
    with pytest.raises(ValueError, match="unknown optimization level"):
        sonolus_backend.build_engine({**empty_payload, "level": "turbo"})
    with pytest.raises(ValueError, match="exactly the keys"):
        sonolus_backend.build_engine({**empty_payload, "extra": 1})
    missing = {k: v for k, v in empty_payload.items() if k != "rom"}
    with pytest.raises(ValueError, match="exactly the keys"):
        sonolus_backend.build_engine(missing)
    modes = dict(empty_payload["modes"])
    del modes["tutorial"]
    with pytest.raises(ValueError, match="invalid payload modes"):
        sonolus_backend.build_engine({**empty_payload, "modes": modes})
