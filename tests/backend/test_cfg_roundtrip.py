"""Marshal round-trips over hand-built CFGs covering every CFG shape.

Faithfulness is checked modulo the exporter's deterministic temp renumbering
(``canon_text``); ``assert_idempotent`` additionally pins byte-for-byte
export/import stability.
"""

from __future__ import annotations

import math
import struct

import pytest

from sonolus.backend._opt import ir  # noqa: PLC2701
from sonolus.backend.blocks import PlayBlock
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock
from tests.backend._roundtrip_helpers import assert_faithful, assert_idempotent, roundtrip


def _scalar(name):
    return BlockPlace(TempBlock(name, 1), 0, 0)


def test_empty_exit_block():
    b0 = BasicBlock()
    assert_faithful(b0)
    assert cfg_to_text(roundtrip(b0)).strip() == "0:\n  goto exit".strip()


def test_unconditional_edge():
    b0 = BasicBlock()
    b1 = BasicBlock()
    b0.connect_to(b1, None)
    rt = assert_faithful(b0)
    assert "goto 1" in cfg_to_text(rt)


def test_two_way_branch():
    b0 = BasicBlock()
    t = BasicBlock()
    f = BasicBlock()
    b0.statements = [IRSet(_scalar("v1"), IRPureInstr(Op.Less, [IRGet(_scalar("v0")), IRConst(3)]))]
    b0.test = IRGet(_scalar("v1"))
    b0.connect_to(f, 0)
    b0.connect_to(t, None)
    text = cfg_to_text(assert_faithful(b0))
    assert "if " in text
    assert " else " in text


def test_multiway_with_default():
    b0 = BasicBlock()
    targets = [BasicBlock() for _ in range(4)]
    b0.test = IRGet(_scalar("v0"))
    for c, blk in zip([0, 1, 2], targets[:3], strict=True):
        b0.connect_to(blk, c)
    b0.connect_to(targets[3], None)
    text = cfg_to_text(assert_faithful(b0))
    assert "goto when" in text
    assert "default ->" in text


def test_multiway_without_default():
    # Generator-style default-less multi-way block (missing default == exit).
    b0 = BasicBlock()
    targets = [BasicBlock() for _ in range(3)]
    b0.test = IRGet(_scalar("v0"))
    for c, blk in zip([0, 1, 2], targets, strict=True):
        b0.connect_to(blk, c)
    text = cfg_to_text(assert_faithful(b0))
    assert "goto when" in text
    assert "default ->" not in text


def test_parallel_edges():
    # Two edges between the same block pair with different conds.
    b0 = BasicBlock()
    dst = BasicBlock()
    other = BasicBlock()
    b0.test = IRGet(_scalar("v0"))
    b0.connect_to(dst, 0)
    b0.connect_to(dst, None)  # parallel: same src->dst pair, different cond
    b0.connect_to(other, 1)
    assert_faithful(b0)
    assert_idempotent(b0)


def test_bare_side_effecting_statement():
    b0 = BasicBlock()
    b0.statements = [IRInstr(Op.Break, [IRConst(1), IRGet(_scalar("v0"))])]
    rt = assert_faithful(b0)
    assert "Break(1, " in cfg_to_text(rt)


def test_irset_of_effectful_instr_random():
    b0 = BasicBlock()
    b0.statements = [IRSet(_scalar("v1"), IRInstr(Op.Random, [IRConst(0), IRConst(1)]))]
    rt = assert_faithful(b0)
    assert "Random(0, 1)" in cfg_to_text(rt)


def test_pointer_deref_block_is_blockplace():
    b0 = BasicBlock()
    # block is itself a place (pointer): deref p at a dynamic index.
    ptr = _scalar("p")
    idx = _scalar("i")
    place = BlockPlace(block=ptr, index=idx, offset=0)
    b0.statements = [IRSet(place, IRConst(5))]
    rt = assert_faithful(b0)
    assert_idempotent(b0)
    assert "[i]" in cfg_to_text(b0)  # sanity of the source shape
    _ = rt


def test_array_temps():
    b0 = BasicBlock()
    arr = TempBlock("a3", 8)
    b0.statements = [
        IRSet(BlockPlace(arr, 2, 0), IRConst(1)),  # constant index -> offset folds
        IRSet(BlockPlace(arr, IRGet(_scalar("v0")), 0), IRConst(2)),  # dynamic index
        IRSet(_scalar("out"), IRGet(BlockPlace(arr, IRGet(_scalar("v0")), 1))),  # dynamic + offset
    ]
    rt = assert_faithful(b0)
    text = cfg_to_text(rt)
    assert "[2]" in text  # folded constant index
    assert " + 1]" in text  # dynamic index with offset


def test_size0_temp():
    b0 = BasicBlock()
    e = TempBlock("e", 0)
    b0.statements = [IRSet(BlockPlace(e, 0, 0), IRConst(0))]
    rt = assert_faithful(b0)
    assert "[0]" in cfg_to_text(rt)


def test_nary_add_is_binarized():
    b0 = BasicBlock()
    b0.statements = [
        IRSet(_scalar("v1"), IRPureInstr(Op.Add, [IRConst(1), IRConst(2), IRConst(3), IRConst(4)]))
    ]
    rt = roundtrip(b0)
    text = cfg_to_text(rt)
    # left-to-right binarization: ((1 + 2) + 3) + 4
    assert "((1 + 2) + 3) + 4" in text


def test_nary_multiply_and_mod_binarized():
    b0 = BasicBlock()
    b0.statements = [
        IRSet(_scalar("m"), IRPureInstr(Op.Multiply, [IRGet(_scalar("a")), IRGet(_scalar("b")), IRGet(_scalar("c"))])),
    ]
    rt = roundtrip(b0)
    # a/b/c are renumbered v0/v1/v2 by first-touch; left-to-right binary spine.
    assert "(v0 * v1) * v2" in cfg_to_text(rt)
    assert_idempotent(b0)


def test_raw_int_block_without_mode():
    b0 = BasicBlock()
    # Post-optimization temp memory place (raw int 10000) and result markers.
    b0.statements = [
        IRSet(BlockPlace(10000, 0, 5), IRConst(1)),
        IRSet(BlockPlace(-1, 0, 0), IRConst(1)),
    ]
    rt = assert_faithful(b0, mode=None, callback=None)
    text = cfg_to_text(rt)
    assert "10000[5]" in text
    assert "-1[0]" in text


def test_raw_int_block_with_mode_resolves_writability_but_keeps_display():
    b0 = BasicBlock()
    b0.statements = [IRSet(BlockPlace(10000, 0, 3), IRConst(1))]
    # With a mode, 10000 resolves to TemporaryMemory (writable) for semantics,
    # but the display stays a raw int so text round-trips exactly.
    rt = roundtrip(b0, mode=Mode.PLAY, callback="updateSequential")
    assert "10000[3]" in cfg_to_text(rt)


def test_blockdata_enum_block():
    b0 = BasicBlock()
    b0.statements = [IRSet(_scalar("v0"), IRGet(BlockPlace(PlayBlock.EntityData, 4, 0)))]
    rt = roundtrip(b0, mode=Mode.PLAY, callback="updateSequential")
    assert "EntityData[4]" in cfg_to_text(rt)


def test_block_test_expression():
    b0 = BasicBlock()
    t = BasicBlock()
    f = BasicBlock()
    b0.test = IRPureInstr(Op.Equal, [IRGet(_scalar("v0")), IRConst(6.0)])
    b0.connect_to(f, 0)
    b0.connect_to(t, None)
    rt = assert_faithful(b0)
    assert "== 6.0" in cfg_to_text(rt)


def _first_value_bits(cfg):
    stmt = cfg.statements[0]
    return struct.pack("<d", float(stmt.value.value))


def test_nan_inf_consts_bitlevel_roundtrip():
    for value in (float("nan"), float("inf"), float("-inf")):
        b0 = BasicBlock()
        b0.statements = [IRSet(_scalar("v0"), IRConst(value))]
        rt = roundtrip(b0)
        got = float(rt.statements[0].value.value)
        if math.isnan(value):
            assert math.isnan(got)
            assert struct.pack("<d", got) == struct.pack("<d", math.nan)
        else:
            assert struct.pack("<d", got) == struct.pack("<d", value)


def test_int_vs_float_const_display_preserved():
    # NB: IRConst(3) and IRConst(3.0) share a mutated singleton, so a single
    # value can't carry two displays; use distinct values to check both forms.
    b0 = BasicBlock()
    b0.statements = [
        IRSet(_scalar("i"), IRConst(7)),
        IRSet(_scalar("f"), IRConst(3.5)),
        IRSet(_scalar("g"), IRPureInstr(Op.Add, [IRConst(3.0), IRConst(4)])),
    ]
    text = cfg_to_text(roundtrip(b0))
    assert "<- 7\n" in text  # int display
    assert "3.5" in text
    assert "3.0 + 4" in text  # 3.0 float display, 4 int display


def test_self_loop():
    b0 = BasicBlock()
    b1 = BasicBlock()
    b0.connect_to(b1, None)
    b1.statements = [IRSet(_scalar("v0"), IRPureInstr(Op.Add, [IRGet(_scalar("v0")), IRConst(1)]))]
    b1.test = IRGet(_scalar("v0"))
    b1.connect_to(b1, 0)  # self loop
    exit_b = BasicBlock()
    b1.connect_to(exit_b, None)
    assert_faithful(b0)
    assert_idempotent(b0)


# --------------------------------------------------------------------------
# Negative marshal-in validation: the marshal-in contract rejects malformed input
# with a clear error rather than miscompiling. Each of ir.pyx's input rejection
# paths is covered here.
# --------------------------------------------------------------------------

def test_marshal_in_rejects_prepopulated_phis():
    # Marshal-in input is non-SSA; a block carrying phis is rejected.
    b0 = BasicBlock()
    b0.phis = {TempBlock("x", 1): {}}
    with pytest.raises(ValueError, match=r"block\.phis must be empty"):
        ir.marshal_in(b0, None, None)


def test_marshal_in_rejects_ssa_place():
    b0 = BasicBlock()
    b0.statements = [IRSet(SSAPlace("x", 0), IRConst(1))]
    with pytest.raises(ValueError, match="SSA places are not valid marshal-in input"):
        ir.marshal_in(b0, None, None)


def test_marshal_in_rejects_unsupported_ir_value():
    b0 = BasicBlock()
    b0.statements = [IRSet(_scalar("x"), "not-an-ir-node")]
    with pytest.raises(ValueError, match="Unsupported IR value"):
        ir.marshal_in(b0, None, None)


def test_marshal_in_rejects_unsupported_block_value():
    b0 = BasicBlock()
    b0.statements = [IRSet(BlockPlace("bad-block", 0, 0), IRConst(1))]
    with pytest.raises(ValueError, match="Unsupported block value"):
        ir.marshal_in(b0, None, None)
