"""Tests for fused read-modify-write ops.

Two fusion peepholes are exercised:

* place-based (``lower.fuse_rmw``, post-allocation): ``Set(p, BinOp(Get(p), w))``
  -> ``Set<BinOp>(p, w)`` (and ``+/- 1`` -> ``IncrementPost``/``DecrementPost``);
* op-level Pointed/Shifted (``midend._fuse_ptr_rmw``, SSA after GVN):
  ``SetPointed(a,b,c, BinOp(GetPointed(a,b,c), w))`` -> ``SetAddPointed(a,b,c,w)``
  and the Shifted analogue.

Unit tests check that the expected fused op appears in the exported CFG text (and
that the negative cases do NOT fuse); the semantic tests run hand-built programs
through the full pipeline at every level and assert the interpreted result --
return value, DebugLog stream, and ALL observable memory -- equals the un-fused
MINIMAL reference bit-for-bit (a true differential, since MINIMAL never fuses).
"""

from __future__ import annotations

import struct

import pytest
from sonolus.backend._opt.ir import marshal_in, to_basic_blocks  # noqa: PLC2701
from sonolus.backend._opt.lower import run_fuse_rmw  # noqa: PLC2701

from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
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
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text
from sonolus.backend.place import BlockPlace, TempBlock

OPT_LEVELS = (FAST_PASSES, STANDARD_PASSES)

# Observable plain int blocks (never allocated to; distinct from temp block 10000).
A, B = 20, 21

# binary operator -> (scalar fused op, python-ish check name) used by the unit tests.
BINOP_TO_SCALAR = {
    Op.Add: "SetAdd",
    Op.Subtract: "SetSubtract",
    Op.Multiply: "SetMultiply",
    Op.Divide: "SetDivide",
    Op.Mod: "SetMod",
    Op.Rem: "SetRem",
    Op.Power: "SetPower",
}


def _text(cfg) -> str:
    return cfg_to_text(cfg)


def _std(build) -> str:
    return _text(run_passes(build(), STANDARD_PASSES, OptimizerConfig()))


# ==========================================================================
# Place-based fusion: each binop fuses (exported text shows the fused op).
# ==========================================================================


@pytest.mark.parametrize("binop", list(BINOP_TO_SCALAR), ids=lambda op: op.name)
def test_each_binop_fuses_on_obs_block(binop):
    fused_name = BINOP_TO_SCALAR[binop]

    def build():
        # Seed then RMW an observable (writable) block: a reliable fusion trigger.
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRConst(9)),
                IRSet(BlockPlace(A, 0), IRPureInstr(binop, [IRGet(BlockPlace(A, 0)), IRConst(3)])),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    for level in OPT_LEVELS:
        text = _text(run_passes(build(), level, OptimizerConfig()))
        assert f"{fused_name}(" in text, (level, text)


def test_increment_special_case():
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRConst(5)),
                IRSet(BlockPlace(A, 0), IRPureInstr(Op.Add, [IRGet(BlockPlace(A, 0)), IRConst(1)])),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    text = _std(build)
    assert "IncrementPost(" in text
    assert "SetAdd(" not in text


def test_decrement_special_case():
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRConst(5)),
                IRSet(BlockPlace(A, 0), IRPureInstr(Op.Subtract, [IRGet(BlockPlace(A, 0)), IRConst(1)])),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    text = _std(build)
    assert "DecrementPost(" in text
    assert "SetSubtract(" not in text


def test_add_by_non_one_constant_is_not_increment():
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRConst(5)),
                IRSet(BlockPlace(A, 0), IRPureInstr(Op.Add, [IRGet(BlockPlace(A, 0)), IRConst(2)])),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    text = _std(build)
    assert "SetAdd(" in text
    assert "IncrementPost(" not in text


# ==========================================================================
# Place-based fusion: negative cases (must NOT fuse).
# ==========================================================================


def test_nary_add_does_not_fuse():
    # A 3-arg Add stays n-ary after tree emission (fuse requires a binary value).
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRConst(9)),
                IRSet(
                    BlockPlace(A, 0),
                    IRPureInstr(
                        Op.Add,
                        [IRGet(BlockPlace(A, 0)), IRGet(BlockPlace(A, 1)), IRGet(BlockPlace(A, 2))],
                    ),
                ),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    assert "SetAdd(" not in _std(build)


def test_get_in_second_arg_does_not_fuse():
    # ``Set(p, Subtract(w, Get(p)))`` -- the read is args[1], not args[0]; this is
    # not an ``Set<BinOp>(p, w)`` RMW (Subtract is non-commutative), so never fuse.
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRConst(9)),
                IRSet(BlockPlace(A, 0), IRPureInstr(Op.Subtract, [IRConst(5), IRGet(BlockPlace(A, 0))])),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    text = _std(build)
    assert "SetSubtract(" not in text
    assert "DecrementPost(" not in text


def test_different_place_does_not_fuse():
    # ``Set(A[0], Add(Get(A[1]), w))`` reads a DIFFERENT place than it stores.
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 1), IRConst(9)),
                IRSet(BlockPlace(A, 0), IRPureInstr(Op.Add, [IRGet(BlockPlace(A, 1)), IRConst(3)])),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    assert "SetAdd(" not in _std(build)


def test_random_index_places_do_not_fuse():
    # run_fuse_rmw (the exported allocate+fuse test API) skips treeify, so an inline
    # Random draw can survive in a place index -- unlike production, where treeify
    # materializes it. Two DISTINCT Random draws must never compare structurally
    # equal (each is a separate observable event: _values_equal only compares pure
    # operand trees), so Set(p[Random], Add(Get(p[Random]), 1)) must NOT fuse --
    # fusing would drop one draw and alias two independent cells.
    def build():
        return BasicBlock(
            statements=[
                IRSet(
                    BlockPlace(A, IRInstr(Op.Random, [IRConst(0), IRConst(4)])),
                    IRPureInstr(
                        Op.Add,
                        [
                            IRGet(BlockPlace(A, IRInstr(Op.Random, [IRConst(0), IRConst(4)]))),
                            IRConst(1),
                        ],
                    ),
                ),
            ]
        )

    text = cfg_to_text(run_fuse_rmw(build()))
    assert "IncrementPost(" not in text, text
    assert "SetAdd(" not in text, text
    assert text.count("Random(") >= 2, text  # both independent draws survive


def test_minimal_never_fuses():
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRConst(9)),
                IRSet(BlockPlace(A, 0), IRPureInstr(Op.Add, [IRGet(BlockPlace(A, 0)), IRConst(3)])),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    text = _text(run_passes(build(), MINIMAL_PASSES, OptimizerConfig()))
    assert "SetAdd(" not in text
    assert "IncrementPost(" not in text


def test_array_element_and_temp_scalar_fuse():
    # A temp array element and a temp scalar both RMW to block 10000 after allocation.
    def build():
        arr = TempBlock("arr", 3)
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(arr, 1), IRConst(7)),
                IRSet(BlockPlace(arr, 1), IRPureInstr(Op.Add, [IRGet(BlockPlace(arr, 1)), IRConst(5)])),
                IRSet(BlockPlace(A, 0), IRGet(BlockPlace(arr, 1))),  # observe
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    text = _std(build)
    assert "SetAdd(10000, 1, 5)" in text


# ==========================================================================
# Op-level Pointed / Shifted fusion (SSA, after GVN).
# ==========================================================================


def _get_pointed(block, index, offset):
    return IRInstr(Op.GetPointed, [IRConst(block), IRConst(index), IRConst(offset)])


def _set_pointed(block, index, offset, value):
    return IRInstr(Op.SetPointed, [IRConst(block), IRConst(index), IRConst(offset), value])


def _get_shifted(block, off, index, stride):
    return IRInstr(Op.GetShifted, [IRConst(block), IRConst(off), IRConst(index), IRConst(stride)])


def _set_shifted(block, off, index, stride, value):
    return IRInstr(Op.SetShifted, [IRConst(block), IRConst(off), IRConst(index), IRConst(stride), value])


@pytest.mark.parametrize(
    ("binop", "fused"),
    [
        (Op.Add, "SetAddPointed"),
        (Op.Subtract, "SetSubtractPointed"),
        (Op.Multiply, "SetMultiplyPointed"),
        (Op.Divide, "SetDividePointed"),
        (Op.Mod, "SetModPointed"),
        (Op.Rem, "SetRemPointed"),
        (Op.Power, "SetPowerPointed"),
    ],
)
def test_pointed_binops_fuse(binop, fused):
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(30, 0), IRConst(31)),
                IRSet(BlockPlace(30, 1), IRConst(4)),
                IRSet(BlockPlace(31, 5), IRConst(50)),
                _set_pointed(30, 0, 1, IRPureInstr(binop, [_get_pointed(30, 0, 1), IRConst(3)])),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    for level in OPT_LEVELS:
        assert f"{fused}(" in _text(run_passes(build(), level, OptimizerConfig()))


def test_shifted_fuses_and_increment_pointed():
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(40, 14), IRConst(100)),
                _set_shifted(40, 2, 3, 4, IRPureInstr(Op.Multiply, [_get_shifted(40, 2, 3, 4), IRConst(2)])),
                IRSet(BlockPlace(30, 0), IRConst(31)),
                IRSet(BlockPlace(30, 1), IRConst(0)),
                _set_pointed(30, 0, 0, IRPureInstr(Op.Add, [_get_pointed(30, 0, 0), IRConst(1)])),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    text = _std(build)
    assert "SetMultiplyShifted(" in text
    assert "IncrementPostPointed(" in text


def test_intervening_side_effect_blocks_pointed_fusion():
    # A DebugLog between the GetPointed value (held in a scalar) and the SetPointed
    # forces the mid-end guard to decline fusion.
    def sc(n):
        return BlockPlace(TempBlock(n, 1), 0, 0)

    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(30, 0), IRConst(31)),
                IRSet(BlockPlace(30, 1), IRConst(0)),
                IRSet(BlockPlace(31, 1), IRConst(50)),
                IRSet(sc("t"), _get_pointed(30, 0, 1)),
                IRInstr(Op.DebugLog, [IRConst(999)]),
                _set_pointed(30, 0, 1, IRPureInstr(Op.Add, [IRGet(sc("t")), IRConst(6)])),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    assert "SetAddPointed(" not in _std(build)
    # ... and it stays correct (differential below covers value equality).
    _assert_levels_match(build, blocks=(31,))


# ==========================================================================
# Semantic differential: full pipeline vs un-fused MINIMAL reference.
# ==========================================================================

_ROM = [float("nan"), float("inf"), float("-inf")]


def _fb(x: float) -> bytes:
    x = float(x)
    if x == 0.0:  # +0.0 / -0.0 collapse is a policy exception
        x = 0.0
    return struct.pack(">d", x)


def _observe(it: Interpreter, ret: float, blocks, length: int = 8) -> tuple:
    key = [_fb(ret), b"log", *(_fb(x) for x in it.log), b"mem"]
    for block in blocks:
        key.extend(_fb(it.get(block, i)) for i in range(length))
    return tuple(key)


def _interp(build, level, blocks):
    node = cfg_to_engine_node(run_passes(build(), level, OptimizerConfig()))
    it = Interpreter()
    it.blocks[3000] = list(_ROM)
    ret = it.run(node)
    return it, _observe(it, ret, blocks)


def _assert_levels_match(build, blocks=(A, B)):
    ref_it, ref = _interp(build, MINIMAL_PASSES, blocks)
    for level in OPT_LEVELS:
        _, key = _interp(build, level, blocks)
        assert key == ref, f"level {level!r} diverged from the un-fused MINIMAL reference"
    return ref_it


def test_differential_straight_line_all_binops():
    def build():
        stmts = [IRSet(BlockPlace(A, i), IRConst(12 + i)) for i in range(7)]
        for i, binop in enumerate(BINOP_TO_SCALAR):
            stmts.append(IRSet(BlockPlace(A, i), IRPureInstr(binop, [IRGet(BlockPlace(A, i)), IRConst(3)])))
        b0 = BasicBlock(statements=stmts)
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    _assert_levels_match(build)


def test_differential_rmw_in_loop():
    # Accumulate obs[A][0] += 2 over a counted loop -- the RMW is loop-carried so it
    # survives lowering as a real Set(obs, Add(Get(obs), 2)) and fuses.
    def sc(n):
        return BlockPlace(TempBlock(n, 1), 0, 0)

    def build():
        init = BasicBlock(statements=[IRSet(BlockPlace(A, 0), IRConst(0)), IRSet(sc("k"), IRConst(0))])
        header = BasicBlock(test=IRPureInstr(Op.Less, [IRGet(sc("k")), IRConst(5)]))
        body = BasicBlock(
            statements=[IRSet(BlockPlace(A, 0), IRPureInstr(Op.Add, [IRGet(BlockPlace(A, 0)), IRConst(2)]))]
        )
        step = BasicBlock(statements=[IRSet(sc("k"), IRPureInstr(Op.Add, [IRGet(sc("k")), IRConst(1)]))])
        after = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRGet(BlockPlace(A, 0))])])
        init.connect_to(header, None)
        header.connect_to(body, None)
        header.connect_to(after, 0)
        body.connect_to(step, None)
        step.connect_to(header, None)
        return init

    it = _assert_levels_match(build)
    assert it.get(A, 0) == 10.0  # 5 iterations * 2
    # The fusion actually fired at standard.
    assert "SetAdd(" in _std(build)


def test_differential_guarded_rmw_in_switch():
    # A multi-way switch where each arm RMWs a different observable slot; a miss
    # (default-less) exits. Exercises fusion inside guarded control flow.
    def build_for(sel):
        def sc(n):
            return BlockPlace(TempBlock(n, 1), 0, 0)

        def build():
            entry = BasicBlock(
                statements=[
                    IRSet(BlockPlace(A, 0), IRConst(100)),
                    IRSet(BlockPlace(A, 1), IRConst(200)),
                    IRSet(sc("s"), IRConst(sel)),
                ],
                test=IRGet(sc("s")),
            )
            join = BasicBlock()
            for cond, slot, delta in ((0, 0, 5), (1, 1, 7)):
                arm = BasicBlock(
                    statements=[
                        IRSet(
                            BlockPlace(A, slot),
                            IRPureInstr(Op.Add, [IRGet(BlockPlace(A, slot)), IRConst(delta)]),
                        )
                    ]
                )
                entry.connect_to(arm, cond)
                arm.connect_to(join, None)
            return entry

        return build

    for sel in (0, 1, 9):  # 9 -> miss -> exit
        _assert_levels_match(build_for(sel))


def test_differential_pointed_and_shifted():
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(30, 0), IRConst(31)),
                IRSet(BlockPlace(30, 1), IRConst(2)),
                IRSet(BlockPlace(31, 3), IRConst(50)),  # ptr target: get(30,1)+1 = 3
                _set_pointed(30, 0, 1, IRPureInstr(Op.Add, [_get_pointed(30, 0, 1), IRConst(6)])),
                IRSet(BlockPlace(32, 14), IRConst(100)),
                _set_shifted(32, 2, 3, 4, IRPureInstr(Op.Subtract, [_get_shifted(32, 2, 3, 4), IRConst(30)])),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    ref = _assert_levels_match(build, blocks=(31, 32))
    assert ref.get(31, 3) == 56.0
    assert ref.get(32, 14) == 70.0


# ==========================================================================
# Emission-path agreement and marshal round-trip for fused programs.
# ==========================================================================


def test_fused_emit_path_agrees_with_export_then_emit():
    def build():
        arr = TempBlock("arr", 2)
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(arr, 0), IRConst(7)),
                IRSet(BlockPlace(arr, 0), IRPureInstr(Op.Add, [IRGet(BlockPlace(arr, 0)), IRConst(5)])),
                IRSet(BlockPlace(A, 0), IRGet(BlockPlace(arr, 0))),
                IRSet(BlockPlace(A, 1), IRPureInstr(Op.Divide, [IRGet(BlockPlace(A, 1)), IRConst(2)])),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    for level in OPT_LEVELS:
        fused = optimize_and_finalize(build(), level, OptimizerConfig())
        exported = cfg_to_engine_node(run_passes(build(), level, OptimizerConfig()))
        assert fused == exported


def test_fused_export_roundtrip_is_idempotent():
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRConst(9)),
                IRSet(BlockPlace(A, 0), IRPureInstr(Op.Add, [IRGet(BlockPlace(A, 0)), IRConst(3)])),
                IRSet(BlockPlace(A, 1), IRPureInstr(Op.Multiply, [IRGet(BlockPlace(A, 1)), IRConst(2)])),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    cfg = run_passes(build(), STANDARD_PASSES, OptimizerConfig())
    rt = to_basic_blocks(marshal_in(cfg, None, None))
    rt2 = to_basic_blocks(marshal_in(rt, None, None))
    assert cfg_to_text(rt) == cfg_to_text(rt2)
    assert cfg_to_engine_node(cfg) == cfg_to_engine_node(rt)
    # The fused op survived the round-trip.
    assert "SetAdd" in cfg_to_text(cfg)
