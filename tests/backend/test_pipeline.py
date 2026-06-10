# ruff: noqa: PLC2701
"""Rust minimal-pipeline tests (PORT.md T1.3).

1. **pydori budget check**: every pydori callback (all modes, dev and non-dev
   runtime checks — the same enumeration ``tests/regressions/test_project.py``
   uses) is traced, encoded, and compiled through ``run_pipeline(minimal)``.
   Every callback must fit the 4096-slot temporary-memory budget, and the Rust
   allocator's high-water mark must never exceed the legacy ``AllocateBasic``
   equivalent (the sum of unique temp-block sizes after ``CoalesceFlow`` +
   ``UnreachableCodeElimination`` — exactly what the legacy minimal pipeline
   would consume, since ``AllocateBasic`` never reuses slots). The legacy
   minimal level is known to blow the budget on some pydori callbacks (see the
   comment in tests/regressions/test_project.py); this test reports them.

2. **Behavioral sanity**: real traced callbacks run through
   ``run_pipeline(minimal)`` + the Rust interpreter must produce the same
   result and debug log as the legacy minimal pipeline + the legacy Python
   interpreter (RNG bridged via a recorded tape).

Skipped when the ``sonolus_backend`` extension is not installed.
"""

import pytest

sonolus_backend = pytest.importorskip("sonolus_backend")

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.encode import encode_cfg
from sonolus.backend.finalize import cfg_to_engine_node
from sonolus.backend.interpret import Interpreter as LegacyInterpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.optimize.dead_code import UnreachableCodeElimination
from sonolus.backend.optimize.flow import traverse_cfg_preorder
from sonolus.backend.optimize.optimize import MINIMAL_PASSES
from sonolus.backend.optimize.passes import OptimizerConfig, run_passes
from sonolus.backend.optimize.simplify import CoalesceFlow
from sonolus.backend.place import BlockPlace, TempBlock
from sonolus.build.compile import callback_to_cfg
from sonolus.script.internal.callbacks import (
    navigate_callback,
    preprocess_callback,
    update_callback,
    update_spawn_callback,
)
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from tests.backend.test_cfg_roundtrip import REAL_CALLBACKS
from tests.backend.test_emit_ab import assert_values_equal, rng_tape_recorder, trace_cfg_with_rom
from tests.regressions import pydori_project

TEMP_SIZE = 4096


def _iter_pydori_callbacks():
    """Yields ``(label, frontend_cfg)`` for every pydori callback, mirroring the
    enumeration in tests/regressions/test_project.py (all modes, dev/non-dev)."""
    engine = pydori_project.engine.data
    mode_specs = [
        (Mode.PLAY, engine.play.archetypes, None),
        (Mode.WATCH, engine.watch.archetypes, [(update_spawn_callback, engine.watch.update_spawn)]),
        (Mode.PREVIEW, engine.preview.archetypes, None),
        (
            Mode.TUTORIAL,
            None,
            [
                (preprocess_callback, engine.tutorial.preprocess),
                (navigate_callback, engine.tutorial.navigate),
                (update_callback, engine.tutorial.update),
            ],
        ),
    ]
    for mode, archetypes, global_callbacks in mode_specs:
        for dev in (False, True):
            suffix = "_dev" if dev else ""
            runtime_checks = RuntimeChecks.NOTIFY_AND_TERMINATE if dev else RuntimeChecks.NONE
            for archetype in archetypes or []:
                archetype._init_fields()
                callback_items = [
                    (cb_name, cb_info, getattr(archetype, cb_name))
                    for cb_name, cb_info in archetype._supported_callbacks_.items()
                    if getattr(archetype, cb_name) not in archetype._default_callbacks_
                ]
                for cb_name, cb_info, cb in callback_items:
                    project_state = ProjectContextState(runtime_checks=runtime_checks)
                    mode_state = ModeContextState(
                        mode,
                        {a: i for i, a in enumerate(archetypes)} if archetypes is not None else None,
                    )
                    cfg = callback_to_cfg(project_state, mode_state, cb, cb_info.name, archetype)
                    yield f"{mode.name.lower()}/{archetype.__name__}.{cb_name}{suffix}", cfg
            for cb_info, cb in global_callbacks or []:
                project_state = ProjectContextState(runtime_checks=runtime_checks)
                mode_state = ModeContextState(
                    mode,
                    {a: i for i, a in enumerate(archetypes)} if archetypes is not None else None,
                )
                cfg = callback_to_cfg(project_state, mode_state, cb, cb_info.name, None)
                yield f"{mode.name.lower()}/global.{cb_info.name}{suffix}", cfg


def _allocate_basic_slots(entry) -> int:
    """The legacy ``AllocateBasic`` slot consumption for a CFG: the sum of
    unique temp-block sizes over every reachable statement/test (AllocateBasic
    assigns each first-encountered temp a fresh range and never reuses).
    Iterative (frontend trees can be deep)."""
    seen: set[TempBlock] = set()
    total = 0

    def scan(root):
        nonlocal total
        stack = [root]
        while stack:
            cur = stack.pop()
            match cur:
                case IRPureInstr(args=args) | IRInstr(args=args):
                    stack.extend(args)
                case IRGet(place=place):
                    stack.append(place)
                case IRSet(place=place, value=value):
                    stack.append(place)
                    stack.append(value)
                case BlockPlace(block=block, index=index):
                    if isinstance(block, TempBlock):
                        if block not in seen:
                            seen.add(block)
                            total += block.size
                    else:
                        stack.append(block)
                    stack.append(index)
                case _:
                    pass

    for block in traverse_cfg_preorder(entry):
        for statement in block.statements:
            scan(statement)
        scan(block.test)
    return total


def test_pydori_all_callbacks_compile_within_budget_and_beat_allocate_basic():
    results = []
    failures = []
    for label, cfg in _iter_pydori_callbacks():
        # Encode the frontend CFG first: the legacy passes mutate it in place.
        data = encode_cfg(cfg)
        try:
            _nodes, stats = sonolus_backend.run_pipeline_stats(data, "minimal")
            rust_slots = stats["temp_slots_used"]
        except ValueError as e:  # budget exceeded -> hard failure
            failures.append(f"{label}: Rust minimal pipeline failed: {e}")
            continue
        # Legacy minimal equivalent: CoalesceFlow + UCE, then AllocateBasic's
        # no-reuse consumption (sum of unique temp sizes).
        cleaned = run_passes(cfg, (CoalesceFlow(), UnreachableCodeElimination()), OptimizerConfig())
        legacy_slots = _allocate_basic_slots(cleaned)
        results.append((label, rust_slots, legacy_slots))
        if rust_slots > legacy_slots:
            failures.append(f"{label}: Rust used {rust_slots} slots > legacy AllocateBasic {legacy_slots}")
        if rust_slots > TEMP_SIZE:
            failures.append(f"{label}: Rust used {rust_slots} slots > budget {TEMP_SIZE}")

    assert results, "no pydori callbacks were enumerated"
    assert not failures, "\n".join(failures)

    worst = sorted(results, key=lambda r: -r[1])[:5]
    legacy_over_budget = [(label, legacy) for label, _, legacy in results if legacy >= TEMP_SIZE]
    total_rust = sum(r[1] for r in results)
    total_legacy = sum(r[2] for r in results)
    print(f"\npydori callbacks compiled at minimal: {len(results)}")
    print(f"total slots: rust={total_rust} legacy_allocate_basic={total_legacy}")
    print(f"max rust slots: {max(r[1] for r in results)}")
    print("top 5 rust slot users:")
    for label, rust_slots, legacy_slots in worst:
        print(f"  {label}: rust={rust_slots} legacy={legacy_slots}")
    print(f"legacy-minimal over-budget callbacks (>= {TEMP_SIZE}): {len(legacy_over_budget)}")
    for label, legacy in sorted(legacy_over_budget, key=lambda r: -r[1])[:10]:
        print(f"  {label}: legacy={legacy}")


@pytest.mark.parametrize("callback", REAL_CALLBACKS, ids=lambda fn: fn.__name__)
def test_minimal_pipeline_behavior_matches_legacy_minimal(callback):
    cfg, rom_values = trace_cfg_with_rom(callback)
    data = encode_cfg(cfg)  # before run_passes mutates the CFG

    legacy_cfg = run_passes(cfg, MINIMAL_PASSES, OptimizerConfig())
    entry = cfg_to_engine_node(legacy_cfg)
    legacy = LegacyInterpreter()
    legacy.blocks[int(PlayBlock.EngineRom)] = list(rom_values)
    tape: list[float] = []
    with rng_tape_recorder(tape):
        legacy_result = legacy.run(entry)

    rust_nodes = sonolus_backend.run_pipeline(data, "minimal")
    rust = sonolus_backend.Interpreter(tape=tape)
    rust.set_block(int(PlayBlock.EngineRom), list(rom_values))
    rust_result = rust.run(rust_nodes)

    assert_values_equal(rust_result, legacy_result, "result")
    rust_log = rust.log
    assert len(rust_log) == len(legacy.log), "log length mismatch"
    for i, (actual, expected) in enumerate(zip(rust_log, legacy.log, strict=True)):
        assert_values_equal(actual, expected, f"log[{i}]")
