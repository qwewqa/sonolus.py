"""Pins RUNTIME_CONSTANT_BLOCKS membership and its effect on emission.

The set marks blocks whose constant-index reads are fixed after load, so the runtime folds pure arithmetic
over them to a single push. The membership pin is written out by hand rather than derived from the enum
tables, so a wrong entry fails here instead of being read back as expected. ArchetypeScore (5001) has
spec-identical permissions to ArchetypeLife (5000) and must classify the same way; it was added to the
blocks enum after the set was written and was originally left out. Membership follows the permission table
rather than any published statement about the runtime: the Sonolus spec documents folding only for a
write-never block, so the runtime's treatment of 5001 is no better attested than that of ArchetypeLife or
of any other member.
"""

from sonolus.backend._opt import emit, ir  # ruff: ignore[import-private-name]
from sonolus.backend.ir import IRConst, IRGet, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock
from sonolus.backend.place import BlockPlace


def test_runtime_constant_blocks_membership():
    # Level-global blocks that are fixed after load: never writable outside preprocess and not per-entity or
    # per-frame. Named independently of the implementation.
    assert (
        frozenset(
            {
                "RuntimeEnvironment",
                "RuntimeUI",
                "RuntimeUIConfiguration",
                "LevelData",
                "LevelOption",
                "LevelBucket",
                "LevelScore",
                "LevelLife",
                "EngineRom",
                "ArchetypeLife",
                "ArchetypeScore",
                "RuntimeCanvas",
                "PreviewData",
                "PreviewOption",
                "TutorialData",
            }
        )
        == ir.RUNTIME_CONSTANT_BLOCKS
    )


def _emitted_read(block_id: int, cb: str):
    """Emit ``LevelMemory[0] <- LevelMemory[Get(block_id, 0) * 2 + 8]`` and return the read node."""
    index = IRPureInstr(Op.Multiply, [IRGet(BlockPlace(block_id, 0, 0)), IRConst(2)])
    b0 = BasicBlock(statements=[IRSet(BlockPlace(2000, 0, 0), IRGet(BlockPlace(2000, index, 8)))])
    node = emit.emit_cfg(b0, Mode.PLAY, cb)
    set_node = node.args[0].args[0].args[0]  # Block > JumpLoop > Execute > Set
    assert set_node.func == Op.Set
    return set_node.args[2]


def test_archetype_score_read_emits_like_archetype_life():
    # Outside preprocess both blocks are read-only, so an index over either is runtime-constant and the
    # Shifted rewrite declines identically: the emitted trees differ only in the block id read.
    life = _emitted_read(5000, "updateParallel")
    score = _emitted_read(5001, "updateParallel")
    assert life.func == Op.Get
    assert score.func == Op.Get
    assert _swap_block(life, 5000, 5001) == score


def test_archetype_score_read_in_preprocess_is_not_runtime_constant():
    # Both blocks are writable in preprocess, so neither index is runtime-constant there and the Shifted
    # rewrite stays for both.
    life = _emitted_read(5000, "preprocess")
    score = _emitted_read(5001, "preprocess")
    assert life.func == Op.GetShifted
    assert score.func == Op.GetShifted


def _swap_block(node, old: int, new: int):
    from sonolus.backend.node import FunctionNode

    if isinstance(node, FunctionNode):
        return FunctionNode(node.func, tuple(_swap_block(a, old, new) for a in node.args))
    if node == old:
        return new
    return node
