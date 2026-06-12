"""Assembly of the single-call engine build payload (PORT.md task T4.1).

This module produces the payload consumed by the Rust ``build_engine`` FFI call
(task T4.2), specified in ``rust/PAYLOAD.md`` (schema version 1): per-mode work
units (callback name, archetype index, order, encoded CFG bytes), mode metadata
JSON strings mirroring the legacy mode-data shapes minus per-node data, the ROM as
an f64 list, and the optimization-level selection.

Everything here is a pure function of its inputs. Tracing is sequential and
deterministic (play -> watch -> preview -> tutorial; within a mode, canonical unit
order), matching the legacy GIL-build ``compile_mode`` order exactly — the shared
``ProjectContextState``/ROM makes this order meaning-bearing (PAYLOAD.md §8).

Nothing in the product path imports this module yet; ``package_engine`` is wired
onto it in task T4.3.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Any

from sonolus.backend.encode import encode_cfg
from sonolus.backend.mode import Mode
from sonolus.backend.optimize import optimize
from sonolus.backend.optimize.passes import CompilerPass
from sonolus.build.compile import callback_to_cfg
from sonolus.build.engine import (
    build_buckets,
    build_effects,
    build_engine_configuration,
    build_instructions,
    build_particles,
    build_skin,
)
from sonolus.script.archetype import _BaseArchetype
from sonolus.script.engine import (
    EngineData,
    empty_play_mode,
    empty_preview_mode,
    empty_tutorial_mode,
    empty_watch_mode,
)
from sonolus.script.internal.callbacks import (
    CallbackInfo,
    navigate_callback,
    preprocess_callback,
    update_callback,
    update_spawn_callback,
)
from sonolus.script.internal.context import ModeContextState, ProjectContextState
from sonolus.script.project import BuildConfig

PAYLOAD_SCHEMA_VERSION = 1
"""The payload schema version produced by this module (see rust/PAYLOAD.md)."""

LEVELS = ("minimal", "fast", "standard")
"""The optimization levels representable in the payload."""


def dedup_key(cfg: bytes) -> bytes:
    """Returns the reference intra-call dedup key for encoded CFG bytes.

    The dedup identity is the encoded byte string itself (PAYLOAD.md §5, decision
    D6); this collision-resistant hash of it is the reference key used by tests
    and diagnostics. Equal keys imply (up to SHA-256 collision resistance) equal
    bytes and therefore identical compilation output within one call.
    """
    return hashlib.sha256(cfg).digest()


def level_from_passes(passes: Sequence[CompilerPass]) -> str:
    """Maps a legacy ``BuildConfig.passes`` value to a payload level name.

    Only the three published pass tuples are representable in the payload
    (PAYLOAD.md §6); arbitrary custom pass sequences raise.

    Args:
        passes: The pass sequence to map.

    Returns:
        ``"minimal"``, ``"fast"``, or ``"standard"``.

    Raises:
        ValueError: If the sequence is not one of the published pass tuples.
    """
    for level, known in (
        ("minimal", optimize.MINIMAL_PASSES),
        ("fast", optimize.FAST_PASSES),
        ("standard", optimize.STANDARD_PASSES),
    ):
        if tuple(passes) == tuple(known):
            return level
    raise ValueError("Custom pass sequences are not representable in the build payload")


def assemble_engine_payload(
    engine: EngineData,
    config: BuildConfig | None = None,
    level: str = "standard",
    project_state: ProjectContextState | None = None,
) -> dict[str, Any]:
    """Assembles the single-call engine build payload (rust/PAYLOAD.md, schema v1).

    Traces every callback of the engine's four modes (substituting the legacy
    empty modes per the config's ``build_*`` flags, like ``package_engine``),
    encodes the frontend CFGs, and assembles the payload for one
    ``build_engine`` FFI call. Performs no optimization and no packaging.

    Args:
        engine: The engine data to build.
        config: The build configuration; defaults to ``BuildConfig()``.
        level: The optimization level: ``"minimal"``, ``"fast"``, or ``"standard"``.
        project_state: The project context state to trace under; a fresh one is
            derived from the config if not provided.

    Returns:
        The payload dict (see rust/PAYLOAD.md §1).

    Raises:
        ValueError: If the level is unknown or a callback declares an unsupported
            non-zero order (matching the legacy ``compile_mode`` error).
    """
    if level not in LEVELS:
        raise ValueError(f"Unknown optimization level: {level!r} (expected one of {', '.join(LEVELS)})")
    config = config or BuildConfig()
    if project_state is None:
        project_state = ProjectContextState.from_build_config(config)

    configuration = build_engine_configuration(engine.options, engine.ui)

    play_mode = engine.play if config.build_play else empty_play_mode()
    watch_mode = engine.watch if config.build_watch else empty_watch_mode()
    preview_mode = engine.preview if config.build_preview else empty_preview_mode()
    tutorial_mode = engine.tutorial if config.build_tutorial else empty_tutorial_mode()

    # Trace order is meaning-bearing (shared ROM; PAYLOAD.md §8): sequential, in
    # payload mode order, matching the legacy GIL build.
    modes = {
        "play": _assemble_mode(
            mode=Mode.PLAY,
            project_state=project_state,
            archetypes=play_mode.archetypes,
            global_callbacks=None,
            tail={
                "skin": build_skin(play_mode.skin),
                "effect": build_effects(play_mode.effects),
                "particle": build_particles(play_mode.particles),
                "buckets": build_buckets(play_mode.buckets),
            },
        ),
        "watch": _assemble_mode(
            mode=Mode.WATCH,
            project_state=project_state,
            archetypes=watch_mode.archetypes,
            global_callbacks=[(update_spawn_callback, watch_mode.update_spawn)],
            tail={
                "skin": build_skin(watch_mode.skin),
                "effect": build_effects(watch_mode.effects),
                "particle": build_particles(watch_mode.particles),
                "buckets": build_buckets(watch_mode.buckets),
            },
        ),
        "preview": _assemble_mode(
            mode=Mode.PREVIEW,
            project_state=project_state,
            archetypes=preview_mode.archetypes,
            global_callbacks=None,
            tail={"skin": build_skin(preview_mode.skin)},
        ),
        "tutorial": _assemble_mode(
            mode=Mode.TUTORIAL,
            project_state=project_state,
            archetypes=[],
            global_callbacks=[
                (preprocess_callback, tutorial_mode.preprocess),
                (navigate_callback, tutorial_mode.navigate),
                (update_callback, tutorial_mode.update),
            ],
            tail={
                "skin": build_skin(tutorial_mode.skin),
                "effect": build_effects(tutorial_mode.effects),
                "particle": build_particles(tutorial_mode.particles),
                "instruction": build_instructions(tutorial_mode.instructions, tutorial_mode.instruction_icons),
            },
        ),
    }

    # The ROM is only final once every mode has been traced (cross-mode coupling,
    # PAYLOAD.md §8). The `or [0]` guard mirrors the legacy `package_rom`.
    rom_values = [float(v) for v in (project_state.rom.values or [0])]

    return {
        "schema": PAYLOAD_SCHEMA_VERSION,
        "level": level,
        "configuration": _dump_json(configuration),
        "rom": rom_values,
        "modes": modes,
    }


def _assemble_mode(
    mode: Mode,
    project_state: ProjectContextState,
    archetypes: list[type[_BaseArchetype]],
    global_callbacks: list[tuple[CallbackInfo, Callable]] | None,
    tail: dict[str, Any],
) -> dict[str, Any]:
    """Assembles one mode's metadata JSON and work-unit list.

    Mirrors the legacy ``compile_mode`` enumeration exactly (base-archetype
    dedup via ``_derived_base_`` in first-encounter order, the supported/
    non-default callback filter, the order-support validation) but stops at the
    traced, encoded CFG instead of compiling.
    """
    mode_state = ModeContextState(mode, archetypes)
    units: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}

    base_archetypes: list[type[_BaseArchetype]] = []
    first_instance_index: dict[type[_BaseArchetype], int] = {}
    for i, a in enumerate(archetypes):
        base = getattr(a, "_derived_base_", a)
        if base not in first_instance_index:
            first_instance_index[base] = i
            base_archetypes.append(base)

    base_archetype_entries: dict[type[_BaseArchetype], dict[str, Any]] = {}
    for archetype in base_archetypes:
        archetype._init_fields()

        imports = []
        for name, import_info in archetype._imported_keys_.items():
            import_entry: dict[str, Any] = {"name": name, "index": import_info.index}
            if import_info.default is not None:
                import_entry["def"] = import_info.default
            imports.append(import_entry)

        entry: dict[str, Any] = {
            "name": archetype.name,
            "hasInput": archetype.is_scored,
            "imports": imports,
        }
        if mode == Mode.PLAY:
            entry["exports"] = [*archetype._exported_keys_]

        callback_items = [
            (cb_name, cb_info, archetype._callbacks_[cb_name])
            for cb_name, cb_info in archetype._supported_callbacks_.items()
            if cb_name in archetype._callbacks_ and archetype._callbacks_[cb_name] not in archetype._default_callbacks_
        ]
        for cb_name, cb_info, cb in callback_items:
            cb_order = getattr(cb, "_callback_order_", 0)
            if not cb_info.supports_order and cb_order != 0:
                raise ValueError(f"Callback '{cb_name}' does not support a non-zero order")
            cfg = callback_to_cfg(project_state, mode_state, cb, cb_info.name, archetype)
            unit_id = len(units)
            units.append(
                {
                    "callback": cb_info.name,
                    "archetype": first_instance_index[archetype],
                    "order": cb_order,
                    "cfg": encode_cfg(cfg),
                }
            )
            entry[cb_info.name] = {"index": unit_id, "order": cb_order}

        base_archetype_entries[archetype] = entry

    for cb_info, cb in global_callbacks or []:
        cfg = callback_to_cfg(project_state, mode_state, cb, cb_info.name, None)
        unit_id = len(units)
        units.append(
            {
                "callback": cb_info.name,
                "archetype": None,
                "order": 0,
                "cfg": encode_cfg(cfg),
            }
        )
        metadata[cb_info.name] = unit_id

    metadata["archetypes"] = [
        {**base_archetype_entries[getattr(a, "_derived_base_", a)], "name": a.name, "hasInput": a.is_scored}
        for a in archetypes
    ]
    metadata["nodes"] = None  # Placeholder; replaced by the node array in Rust (PAYLOAD.md §5).
    metadata.update(tail)

    return {"metadata": _dump_json(metadata), "units": units}


def _dump_json(value: Any) -> str:
    """Compact JSON serialization, identical to the legacy ``package_data``."""
    return json.dumps(value, separators=(",", ":"))
