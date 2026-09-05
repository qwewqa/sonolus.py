"""Tests for frontend context state and lowering it into optimizer CFG blocks."""

import math

from sonolus.backend.ir import IRConst
from sonolus.backend.mode import Mode
from sonolus.backend.optimize.flow import traverse_cfg_preorder
from sonolus.backend.rom import ROM_ZERO_COUNT, ROM_ZERO_START
from sonolus.script.internal.context import (
    CallbackContextState,
    Context,
    ModeContextState,
    ProjectContextState,
    ReadOnlyMemory,
    context_to_cfg,
)


def test_read_only_memory_reserves_contiguous_positive_zeros_after_special_values():
    rom = ReadOnlyMemory()

    assert math.isnan(rom.values[0])
    assert rom.values[1:ROM_ZERO_START] == [float("inf"), float("-inf")]
    assert len(rom.values) == ROM_ZERO_START + ROM_ZERO_COUNT
    assert rom.values[ROM_ZERO_START:] == [0.0] * ROM_ZERO_COUNT
    assert all(math.copysign(1.0, value) == 1.0 for value in rom.values[ROM_ZERO_START:])


def test_read_only_memory_allocates_interned_values_after_reserved_zeros():
    rom = ReadOnlyMemory()

    place = rom[12.5, 13.5]

    assert place.index == ROM_ZERO_START + ROM_ZERO_COUNT
    assert rom.values[-2:] == [12.5, 13.5]


def test_context_to_cfg_visits_each_context_once_with_pending_cross_edges_and_cycles():
    project_state = ProjectContextState()
    mode_state = ModeContextState(Mode.PLAY)
    callback_state = CallbackContextState("update")
    contexts = [Context(project_state, mode_state, callback_state) for _ in range(6)]
    for identifier, context in enumerate(contexts):
        context.test = IRConst(identifier)
    root, a, b, c, d, e = contexts
    root.outgoing.update({0: a, 1: b, None: c})
    c.outgoing[None] = a
    b.outgoing[None] = d
    d.outgoing[None] = e
    a.outgoing[None] = e
    e.outgoing[None] = root

    cfg = context_to_cfg(root)
    blocks = {int(block.test.value): block for block in traverse_cfg_preorder(cfg)}
    adjacency = {
        identifier: {edge.cond: int(edge.dst.test.value) for edge in block.outgoing}
        for identifier, block in blocks.items()
    }

    assert adjacency == {
        0: {0: 1, 1: 2, None: 3},
        1: {None: 5},
        2: {None: 4},
        3: {None: 1},
        4: {None: 5},
        5: {None: 0},
    }
    assert all(len(blocks[identifier].outgoing) == len(edges) for identifier, edges in adjacency.items())
    assert all(not hasattr(context, "outgoing") for context in contexts)
