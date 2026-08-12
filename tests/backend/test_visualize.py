"""Tests for the debug CFG renderers and the optimizer shim's config threading.

Covers `sonolus.backend.optimize.flow.cfg_to_mermaid`, `sonolus.script.debug.visualize_cfg`, and the
documented equivalence between `optimize_and_finalize` and `cfg_to_engine_node(run_passes(...))`.
"""

import pytest

from sonolus.backend._opt import ir  # noqa: PLC2701
from sonolus.backend.blocks import PlayBlock
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.node import format_engine_node
from sonolus.backend.ops import Op
from sonolus.backend.optimize import (
    FAST_PASSES,
    MINIMAL_PASSES,
    STANDARD_PASSES,
    OptimizerConfig,
    cfg_to_engine_node,
    optimize_and_finalize,
    run_passes,
)
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_mermaid, traverse_cfg_reverse_postorder
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock
from sonolus.build.compile import callback_to_cfg
from sonolus.script.array import Array
from sonolus.script.debug import visualize_cfg
from sonolus.script.globals import level_memory
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.internal.visitor import clear_frontend_caches

LEVELS = {"minimal": MINIMAL_PASSES, "standard": STANDARD_PASSES}

# LevelMemory is writable in this callback, so a config naming it keeps the block's stores visible
# to the passes, where a config without a callback name marks every block read-only.
WRITABLE_CALLBACK = "updateSequential"

_memory = level_memory(Array[float, 4])


def _store_load_store():
    # The load must be kept in a temp across the second store: the callback ends with mem[1] == 1.0.
    _memory[0] = 1.0
    loaded = _memory[0]
    _memory[0] = 2.0
    _memory[1] = loaded


def _trace(callback: str) -> BasicBlock:
    clear_frontend_caches()
    project_state = ProjectContextState(runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE)
    mode_state = ModeContextState(Mode.PLAY, None)
    return callback_to_cfg(project_state, mode_state, _store_load_store, callback)


def _shifted_index_cfg(offset: int) -> BasicBlock:
    # DebugLog(LevelData[offset + LevelData[0] * 3]). LevelData is a runtime-constant block that is
    # writable only in 'preprocess', and emission's Get/SetShifted rewrite is decided from that
    # writability, so this address shape is one whose emitted tree depends on the marshal-in config.
    block = BasicBlock()
    index = IRPureInstr(Op.Multiply, [IRGet(BlockPlace(PlayBlock.LevelData, 0)), IRConst(3)])
    block.statements = [IRInstr(Op.DebugLog, [IRGet(BlockPlace(PlayBlock.LevelData, index, offset))])]
    return block


@pytest.mark.parametrize("level_name", ["minimal", "standard"])
@pytest.mark.parametrize("offset", [0, 5])
def test_cfg_to_engine_node_matches_optimize_and_finalize(level_name: str, offset: int):
    # The two entry points are documented as equivalent, so they must emit the same tree for the
    # same config. Emission reads the block writability that marshal-in resolves from the config,
    # so cfg_to_engine_node has to be given the config its CFG was optimized under.
    level = LEVELS[level_name]
    config = OptimizerConfig(mode=Mode.PLAY, callback="preprocess")

    fused = optimize_and_finalize(_shifted_index_cfg(offset), level, config)
    separate = cfg_to_engine_node(run_passes(_shifted_index_cfg(offset), level, config), config)

    assert format_engine_node(separate) == format_engine_node(fused)


def _folded_block_cfg() -> BasicBlock:
    # b = 2000 + 1; DebugLog(LevelMemory[LevelData[3] * 4 + 8]), with LevelData reached through b.
    # Reading through a temp makes the inner address a pointer deref at marshal-in, so the block id
    # is resolved by the passes rather than by marshal-in. The shape above cannot cover that: a block
    # written as a constant is resolved at marshal-in and never reaches the fold.
    pointer = BlockPlace(TempBlock("b", 1), 0, 0)
    index = IRPureInstr(Op.Multiply, [IRGet(BlockPlace(IRGet(pointer), 3, 0)), IRConst(4)])
    block = BasicBlock(
        statements=[
            IRSet(pointer, IRPureInstr(Op.Add, [IRConst(2000), IRConst(1)])),
            IRInstr(Op.DebugLog, [IRGet(BlockPlace(2000, index, 8))]),
        ]
    )
    block.connect_to(BasicBlock(), None)
    return block


@pytest.mark.parametrize("level_name", ["minimal", "standard"])
def test_cfg_to_engine_node_matches_optimize_and_finalize_for_a_folded_block(level_name: str):
    # The same documented equivalence, for the address shape whose block id the passes fold. The
    # separate route exports that block as a plain int and re-marshals it, which re-resolves it
    # against the config; the fused route never leaves the arena, so it has to derive the same
    # answer at the fold itself.
    level = LEVELS[level_name]
    config = OptimizerConfig(mode=Mode.PLAY, callback="updateParallel")

    fused = optimize_and_finalize(_folded_block_cfg(), level, config)
    separate = cfg_to_engine_node(run_passes(_folded_block_cfg(), level, config), config)

    assert format_engine_node(separate) == format_engine_node(fused)


# Minimal is excluded below: it bypasses the mid-end and never lowers out of SSA, so nothing folds
# the index there and both routes agree for free.
FOLDING_LEVELS = {"fast": FAST_PASSES, "standard": STANDARD_PASSES}


def _folded_index_cfg() -> BasicBlock:
    # DebugLog(LevelData[8 + i]) with i = 3 + 0 held in a temp, so the index reaches the passes as a
    # value: a literal one is folded into the place's offset at marshal-in and never gets there.
    # The block id is static from the start, which the shape above cannot cover.
    i = BlockPlace(TempBlock("i", 1), 0, 0)
    block = BasicBlock(
        statements=[
            IRSet(i, IRPureInstr(Op.Add, [IRConst(3), IRConst(0)])),
            IRInstr(Op.DebugLog, [IRGet(BlockPlace(PlayBlock.LevelData, IRGet(i), 8))]),
        ]
    )
    block.connect_to(BasicBlock(), None)
    return block


@pytest.mark.parametrize("level_name", ["fast", "standard"])
def test_cfg_to_engine_node_matches_optimize_and_finalize_for_a_folded_index(level_name: str):
    # The same documented equivalence, for a constant index the passes fold rather than marshal-in.
    # The separate route exports that index as a constant and re-marshals it, where it is baked into
    # the place's offset; the fused route never leaves the arena, so it has to bake it itself.
    level = FOLDING_LEVELS[level_name]
    config = OptimizerConfig(mode=Mode.PLAY, callback="updateParallel")

    fused = optimize_and_finalize(_folded_index_cfg(), level, config)
    separate = cfg_to_engine_node(run_passes(_folded_index_cfg(), level, config), config)

    assert format_engine_node(separate) == format_engine_node(fused)


def _dynamic_block_folded_index_cfg() -> BasicBlock:
    # DebugLog(b[8 + i]) where b = LevelMemory[0] stays a runtime block id and i = 3 + 0 reaches the
    # passes as a value they fold to a constant. The shape above cannot cover this: its block id is
    # static, and constant-index baking dispatches on the place's block kind.
    b = BlockPlace(TempBlock("b", 1), 0, 0)
    i = BlockPlace(TempBlock("i", 1), 0, 0)
    block = BasicBlock(
        statements=[
            IRSet(b, IRGet(BlockPlace(PlayBlock.LevelMemory, 0))),
            IRSet(i, IRPureInstr(Op.Add, [IRConst(3), IRConst(0)])),
            IRInstr(Op.DebugLog, [IRGet(BlockPlace(IRGet(b), IRGet(i), 8))]),
        ]
    )
    block.connect_to(BasicBlock(), None)
    return block


@pytest.mark.parametrize("level_name", ["fast", "standard"])
def test_cfg_to_engine_node_matches_optimize_and_finalize_for_a_dynamic_block(level_name: str):
    # The same documented equivalence, for a place whose block id stays runtime-valued while its index
    # folds. The separate route bakes the folded index into the offset at re-marshal for every place
    # kind, so the fused route has to bake it for dynamic-block places too, not only static ones.
    level = FOLDING_LEVELS[level_name]
    config = OptimizerConfig(mode=Mode.PLAY, callback="updateParallel")

    fused = optimize_and_finalize(_dynamic_block_folded_index_cfg(), level, config)
    separate = cfg_to_engine_node(run_passes(_dynamic_block_folded_index_cfg(), level, config), config)

    assert format_engine_node(separate) == format_engine_node(fused)


def _temp_array_folded_index_cfg() -> BasicBlock:
    # DebugLog(a[8 + i]) with a a 16-slot temp array and i = 3 + 0 folded by the passes. Neither
    # shape above reaches this one: allocation turns a temp place into a real block only after every
    # pass has run, so the address it hands emission was never normalized by one.
    a = TempBlock("a", 16)
    i = BlockPlace(TempBlock("i", 1), 0, 0)
    block = BasicBlock(
        statements=[
            IRSet(BlockPlace(a, 0, 0), IRConst(1.0)),
            IRSet(i, IRPureInstr(Op.Add, [IRConst(3), IRConst(0)])),
            IRInstr(Op.DebugLog, [IRGet(BlockPlace(a, IRGet(i), 8))]),
        ]
    )
    block.connect_to(BasicBlock(), None)
    return block


@pytest.mark.parametrize("level_name", ["fast", "standard"])
def test_cfg_to_engine_node_matches_optimize_and_finalize_for_a_temp_array(level_name: str):
    # The same documented equivalence, for an address the allocator builds. The separate route
    # exports the allocated place and re-marshals it, baking the folded index into the offset, so
    # the fused route has to bake it while the place is still a temp -- or at the rewrite itself.
    level = FOLDING_LEVELS[level_name]
    config = OptimizerConfig(mode=Mode.PLAY, callback="updateParallel")

    fused = optimize_and_finalize(_temp_array_folded_index_cfg(), level, config)
    separate = cfg_to_engine_node(run_passes(_temp_array_folded_index_cfg(), level, config), config)

    assert format_engine_node(separate) == format_engine_node(fused)


@pytest.mark.parametrize("level_name", ["minimal", "standard"])
@pytest.mark.parametrize("offset", [0, 5])
def test_shifted_index_emission_depends_on_the_config(level_name: str, offset: int):
    # Guards the test above against going vacuous. Its whole point is that the config reaches
    # emission, so the CFG it uses has to be one whose emitted tree changes when the config is
    # withheld; if that ever stops being true, the equality there would hold for free. Revisiting
    # what a config without a callback name treats as writable is the change that fires this.
    level = LEVELS[level_name]
    config = OptimizerConfig(mode=Mode.PLAY, callback="preprocess")
    cfg = run_passes(_shifted_index_cfg(offset), level, config)

    assert format_engine_node(cfg_to_engine_node(cfg)) != format_engine_node(cfg_to_engine_node(cfg, config))


def test_visualize_cfg_optimizes_under_the_callback_it_traced():
    # visualize_cfg exists to show what the build compiles for a callback, so its picture has to be
    # the one the build's own config produces. Withholding the callback name makes every block
    # read-only, which lets a load sink past a store to the same address.
    build_config = OptimizerConfig(mode=Mode.PLAY, callback=WRITABLE_CALLBACK)
    as_built = cfg_to_mermaid(run_passes(_trace(WRITABLE_CALLBACK), STANDARD_PASSES, build_config, allocate=False))
    without_callback = cfg_to_mermaid(
        run_passes(_trace(WRITABLE_CALLBACK), STANDARD_PASSES, OptimizerConfig(mode=Mode.PLAY), allocate=False)
    )

    rendered = visualize_cfg(_store_load_store, callback=WRITABLE_CALLBACK, passes="standard")

    assert rendered == as_built
    # Pins the direction: the two configs really do disagree on this source, so the equality above
    # is not one the callback-dropped rendering would satisfy too.
    assert rendered != without_callback


def _dead_phi_cfg() -> BasicBlock:
    entry = BasicBlock()
    target = BasicBlock()
    dead = BasicBlock()  # never reachable from entry -> absent from block_indexes
    entry.connect_to(target)
    dead.connect_to(target)
    target.phis = {SSAPlace("p", 2): {entry: SSAPlace("p", 0), dead: SSAPlace("p", 1)}}
    return entry


def test_cfg_to_mermaid_tolerates_dead_phi_source():
    # A phi referencing a block unreachable from entry must render as a dead-source marker rather
    # than crashing the phi-source sort, which is what cfg_to_text already does
    # (tests/backend/test_cfg_text.py).
    diagram = cfg_to_mermaid(_dead_phi_cfg())  # must not raise

    assert "&lt;dead&gt;" in diagram


def test_cfg_to_mermaid_escapes_the_dead_phi_source_marker():
    # The marker has to survive as text in a real render: cfg_to_mermaid writes HTML labels, so a
    # bare <dead> is parsed as an unknown element and the phi source shows up with no name at all.
    diagram = cfg_to_mermaid(_dead_phi_cfg())

    assert "<pre" in diagram, "labels are no longer HTML, so what needs escaping has changed"
    assert "<dead>" not in diagram


def _scalar(name: str) -> BlockPlace:
    return BlockPlace(TempBlock(name, 1), 0, 0)


def _diamond() -> tuple[BasicBlock, BasicBlock, BasicBlock, BasicBlock]:
    entry = BasicBlock(test=IRGet(BlockPlace(PlayBlock.LevelMemory, 0)))
    taken, not_taken, join = BasicBlock(), BasicBlock(), BasicBlock()
    taken.statements = [IRSet(_scalar("x"), IRConst(10))]
    not_taken.statements = [IRSet(_scalar("x"), IRConst(20))]
    join.statements = [IRInstr(Op.DebugLog, [IRGet(_scalar("x"))])]
    entry.connect_to(not_taken, 0)
    entry.connect_to(taken, None)
    taken.connect_to(join)
    not_taken.connect_to(join)
    return entry, taken, not_taken, join


def _counting_loop() -> BasicBlock:
    # The bound is a memory read so that nothing folds the loop away before build_ssa.
    entry, head, body, exit_ = BasicBlock(), BasicBlock(), BasicBlock(), BasicBlock()
    entry.statements = [IRSet(_scalar("i"), IRConst(0))]
    entry.connect_to(head)
    head.test = IRPureInstr(Op.Less, [IRGet(_scalar("i")), IRGet(BlockPlace(PlayBlock.LevelMemory, 0))])
    head.connect_to(exit_, 0)
    head.connect_to(body, None)
    body.statements = [IRSet(_scalar("i"), IRPureInstr(Op.Add, [IRGet(_scalar("i")), IRConst(1)]))]
    body.connect_to(head)
    exit_.statements = [IRInstr(Op.DebugLog, [IRGet(_scalar("i"))])]
    return entry


def _indexes(entry: BasicBlock) -> dict[BasicBlock, int]:
    return {block: i for i, block in enumerate(traverse_cfg_reverse_postorder(entry))}


def test_cfg_to_mermaid_renders_every_block_edge_and_statement():
    entry, taken, not_taken, join = _diamond()
    index = _indexes(entry)

    diagram = cfg_to_mermaid(entry)
    lines = diagram.splitlines()

    assert lines[0] == "graph"
    assert "    Entry([Entry]) --> 0" in lines
    assert "    Exit([Exit])" in lines
    for block, i in index.items():
        assert f"    {i}[" in diagram
        for statement in block.statements:
            assert str(statement) in diagram
    # The two-way branch renders as a decision node with labelled true/false edges, and the join,
    # having no successor, runs to Exit.
    assert f"    {index[entry]}_ --> |true| {index[taken]}" in lines
    assert f"    {index[entry]}_ --> |false| {index[not_taken]}" in lines
    assert f"    {index[taken]} --> {index[join]}" in lines
    assert f"    {index[join]} --> Exit" in lines


def test_cfg_to_mermaid_labels_multiway_edges_by_condition():
    entry = BasicBlock(test=IRGet(BlockPlace(PlayBlock.LevelMemory, 0)))
    zero, one, other = BasicBlock(), BasicBlock(), BasicBlock()
    entry.connect_to(zero, 0)
    entry.connect_to(one, 1)
    entry.connect_to(other, None)
    index = _indexes(entry)

    diagram = cfg_to_mermaid(entry)

    # A switch labels each edge with its case value and the fallthrough edge with 'default'.
    for label, target in (("0", zero), ("1", one), ("default", other)):
        assert f'>{label}</pre>"| {index[target]}' in diagram


def test_cfg_to_mermaid_renders_phis_with_their_source_blocks():
    # No visualize_cfg level produces phis (minimal stops before build_ssa, the others run
    # lower_from_ssa), so reach the phi branch through the debug phase runner instead.
    ssa = ir.debug_run(_counting_loop(), Mode.PLAY, "updateSequential", phases=["cfg_cleanup", "ssa"])
    index = _indexes(ssa)
    phi_blocks = [block for block in index if block.phis]
    assert phi_blocks, "expected build_ssa to leave a phi in a loop over a memory-bounded counter"

    diagram = cfg_to_mermaid(ssa)

    for block in phi_blocks:
        for dst, sources in block.phis.items():
            rendered = f"{dst} := phi(" + ", ".join(
                f"{index[src_block]}: {src_place}"
                for src_block, src_place in sorted(sources.items(), key=lambda item: index[item[0]])
            )
            assert rendered + ")" in diagram


@pytest.mark.parametrize("passes", ["minimal", "fast", "standard", MINIMAL_PASSES, FAST_PASSES, STANDARD_PASSES])
def test_visualize_cfg_renders_a_mermaid_graph(passes):
    diagram = visualize_cfg(_store_load_store, callback=WRITABLE_CALLBACK, passes=passes)

    assert diagram.splitlines()[0] == "graph"
    assert "    Entry([Entry]) --> 0" in diagram
    assert "    Exit([Exit])" in diagram
    # The traced writes survive at every level: they are stores to a block the game reads back.
    assert "LevelMemory[0] <- 1" in diagram
    assert "LevelMemory[0] <- 2" in diagram


def test_visualize_cfg_rejects_an_unknown_level_name():
    with pytest.raises(ValueError, match=r"Unknown optimization level 'quick' \(expected 'minimal'"):
        visualize_cfg(_store_load_store, callback=WRITABLE_CALLBACK, passes="quick")
