"""Random-CFG differential property tests for the optimizer rewrite (M1+).

Per OPTIMIZER_REWRITE.md section 10 ("Random CFG property tests"): a Hypothesis
generator of small, legal, terminating, deterministic CFG programs
(``tests/backend/_cfg_gen.py``) is run through the public optimizer at every
level, and the interpreted results must agree bit-for-bit. This is the safety
net that gates the mid-end as it lands.

Design
------
* The generator (section 3 input contract) emits shallow three-address CFGs over
  scalar/array ``TempBlock`` registers and plain int memory blocks, with
  sequences, if-diamonds, multi-way switches (contiguous / non-contiguous,
  with and without a default edge), and bounded counting loops.
* For each program the CFG is *regenerated* fresh for every use, because
  ``run_passes`` may mutate its input and ``cfg_to_engine_node`` consumes a CFG.
* Observables captured after interpretation: the return value, the ``DebugLog``
  stream, and a fixed window of the observable int blocks. Floats are compared
  by their IEEE-754 bytes (``struct.pack(">d", ...)``) so ``-0.0``/``NaN`` drift
  is caught.
* The reference is the ``MINIMAL`` level (cfg_cleanup + bump allocation -- the
  least-transforming public path that still produces an emittable, allocated
  CFG); ``FAST`` and ``STANDARD`` must match it exactly. Pipeline determinism is
  checked by running each level twice and comparing ``cfg_to_text``.

Config: ``OptimizerConfig()`` (mode=None) -- the observable int blocks 20/21 are
raw ints, resolved conservatively writable, matching the dual-run suite's config.

Requires the M1 public API (``sonolus.backend.optimize`` level sentinels +
``cfg_to_engine_node``). Validated against the M1 tip; see the module report.

A real M1 bug found by this suite is pinned by
``test_never_written_array_read_aliases_live_temp`` -- see its docstring.
"""

from __future__ import annotations

import struct

import pytest
from hypothesis import HealthCheck, given, settings

from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.optimize import (
    FAST_PASSES,
    MINIMAL_PASSES,
    STANDARD_PASSES,
    OptimizerConfig,
    cfg_to_engine_node,
    run_passes,
)
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text
from sonolus.backend.place import BlockPlace, TempBlock
from tests.backend._cfg_gen import OBS_BLOCKS, OBS_CAPTURE_LEN, build_cfg, count_blocks, programs

LEVELS = (MINIMAL_PASSES, FAST_PASSES, STANDARD_PASSES)
OPT_LEVELS = (FAST_PASSES, STANDARD_PASSES)  # compared against the MINIMAL reference

# Interpreter ROM seed (block 3000): NaN, +Inf, -Inf. finalize lowers non-finite
# constants to ROM reads, so a program that ever emits one reads a real special
# value instead of the lazy -1.0 padding. The random corpus stays finite, but
# seeding is harmless and keeps directed tests robust.
_ROM = [float("nan"), float("inf"), float("-inf")]


def _f(x: float) -> bytes:
    return struct.pack(">d", float(x))


def _observe(it: Interpreter, ret: float, blocks=OBS_BLOCKS, length: int = OBS_CAPTURE_LEN) -> tuple:
    """A bit-exact observable key: return value, DebugLog stream, and memory window."""
    key = [_f(ret), b"log", *(_f(x) for x in it.log), b"mem"]
    for block in blocks:
        key.extend(_f(it.get(block, i)) for i in range(length))
    return tuple(key)


def _interpret(build, level, config, blocks=OBS_BLOCKS, length=OBS_CAPTURE_LEN):
    """Regenerate a fresh CFG, optimize at ``level``, emit, interpret, observe."""
    node = cfg_to_engine_node(run_passes(build(), level, config))
    it = Interpreter()
    it.blocks[3000] = list(_ROM)
    ret = it.run(node)
    return it, _observe(it, ret, blocks, length)


def _assert_levels_agree(build, config=None, blocks=OBS_BLOCKS, length=OBS_CAPTURE_LEN):
    """All levels' observables equal the MINIMAL reference. Returns (ref_it, ref_key)."""
    config = config or OptimizerConfig()
    ref_it, ref = _interpret(build, MINIMAL_PASSES, config, blocks, length)
    for level in OPT_LEVELS:
        _it, key = _interpret(build, level, config, blocks, length)
        assert key == ref, f"level {level!r} observables diverged from the MINIMAL reference"
    return ref_it, ref


def _assert_deterministic(build, config=None):
    """Running a level twice on identically-regenerated CFGs is byte-identical."""
    config = config or OptimizerConfig()
    for level in LEVELS:
        first = cfg_to_text(run_passes(build(), level, config))
        second = cfg_to_text(run_passes(build(), level, config))
        assert first == second, f"level {level!r} pipeline is not deterministic"


# ==========================================================================
# Core property: random programs agree across all levels + determinism.
# ==========================================================================


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(program=programs())
def test_levels_agree_on_random_programs(program):
    def build():
        return build_cfg(program)

    _assert_levels_agree(build)
    _assert_deterministic(build)


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(program=programs(max_depth=4))
def test_levels_agree_on_deeper_programs(program):
    # Deeper nesting -> larger CFGs (more loops/switches/diamonds), same property.
    def build():
        return build_cfg(program)

    _assert_levels_agree(build)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(program=programs())
def test_generated_programs_are_small(program):
    # Sanity on the generator's size envelope (section 10: small CFGs).
    assert count_blocks(build_cfg(program)) <= 60


# ==========================================================================
# Directed regression corpus (hand-built, not Hypothesis).
# ==========================================================================

A = 20  # observable block A
B = 21  # observable block B


def _sc(name):
    return BlockPlace(TempBlock(name, 1), 0, 0)


def _rd(name):
    return IRGet(_sc(name))


def _log(value):
    return IRInstr(Op.DebugLog, [value if not isinstance(value, (int, float)) else IRConst(value)])


def _obs(block, index, value):
    return IRSet(BlockPlace(block, index), value if not isinstance(value, (int, float)) else IRConst(value))


def test_directed_defaultless_switch_falls_to_exit():
    # Default-less multiway {0, 2}; a miss (test not in {0,2}) jumps to exit,
    # skipping the join (log 99 / B[0]=7). Legal per section 3.
    def build_for(sel):
        def build():
            a = BasicBlock(statements=[_obs(A, 0, sel)], test=IRGet(BlockPlace(A, 0)))
            c0 = BasicBlock(statements=[_log(10)])
            c2 = BasicBlock(statements=[_log(20)])
            join = BasicBlock(statements=[_log(99), _obs(B, 0, 7)])
            a.connect_to(c0, 0)
            a.connect_to(c2, 2)
            c0.connect_to(join, None)
            c2.connect_to(join, None)
            return a

        return build

    it0, _ = _assert_levels_agree(build_for(0))
    assert it0.log == [10.0, 99.0]
    assert it0.get(B, 0) == 7.0

    it2, _ = _assert_levels_agree(build_for(2))
    assert it2.log == [20.0, 99.0]

    it5, _ = _assert_levels_agree(build_for(5))  # miss -> exit
    assert it5.log == []
    assert it5.get(B, 0) == -1.0  # join never ran


def test_directed_parallel_edges_same_dst():
    # Two edges from the same block to the same dst with different conds.
    def build():
        a = BasicBlock(test=IRGet(BlockPlace(A, 0)))
        d = BasicBlock(statements=[_log(5), _obs(B, 0, 5)])
        a.connect_to(d, 0)
        a.connect_to(d, None)
        return a

    it, _ = _assert_levels_agree(build)
    assert it.log == [5.0]
    assert it.get(B, 0) == 5.0


def test_directed_constant_test_diamond_folds():
    # Constant if-test -> cleanup folds to the live arm.
    def build_for(const, expected):
        def build():
            a = BasicBlock(test=IRConst(const))
            tb = BasicBlock(statements=[IRSet(_sc("r"), IRConst(111))])
            fb = BasicBlock(statements=[IRSet(_sc("r"), IRConst(222))])
            join = BasicBlock(statements=[_log(_rd("r")), _obs(A, 0, _rd("r"))])
            a.connect_to(fb, 0)
            a.connect_to(tb, None)
            tb.connect_to(join, None)
            fb.connect_to(join, None)
            return a

        it, _ = _assert_levels_agree(build)
        assert it.log == [expected]
        assert it.get(A, 0) == expected

    build_for(1, 111.0)  # true
    build_for(0, 222.0)  # false


def test_directed_empty_body_loop():
    def build():
        init = BasicBlock(statements=[IRSet(_sc("k"), IRConst(0))])
        header = BasicBlock(test=IRPureInstr(Op.Less, [_rd("k"), IRConst(4)]))
        body = BasicBlock()  # empty body
        step = BasicBlock(statements=[IRSet(_sc("k"), IRPureInstr(Op.Add, [_rd("k"), IRConst(1)]))])
        after = BasicBlock(statements=[_log(_rd("k")), _obs(A, 0, _rd("k"))])
        init.connect_to(header, None)
        header.connect_to(body, None)
        header.connect_to(after, 0)
        body.connect_to(step, None)
        step.connect_to(header, None)
        return init

    it, _ = _assert_levels_agree(build)
    assert it.log == [4.0]
    assert it.get(A, 0) == 4.0


def test_directed_array_first_write_init():
    # First write to a slot (is_array_init) makes the whole array live; reads of
    # other (written and unwritten) slots then allocate distinctly. Contrast with
    # the never-written-array bug below.
    def build():
        arr = TempBlock("arr", 3)
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(arr, 0), IRConst(7)),  # first write
                IRSet(BlockPlace(arr, 1), IRConst(8)),
                _log(IRGet(BlockPlace(arr, 0))),  # 7
                _log(IRGet(BlockPlace(arr, 2))),  # unwritten slot of a live array -> -1.0
                _obs(A, 0, IRGet(BlockPlace(arr, 1))),  # 8
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    it, _ = _assert_levels_agree(build)
    assert it.log == [7.0, -1.0]
    assert it.get(A, 0) == 8.0


def test_directed_undefined_scalar_read():
    # A never-written scalar read yields -1.0 consistently across levels (a
    # use-with-no-def stays live from entry, so it is allocated distinctly).
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(_sc("k"), IRConst(5)),
                _log(_rd("u")),  # u never written -> -1.0
                _obs(A, 0, _rd("u")),
                _log(_rd("k")),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    it, _ = _assert_levels_agree(build)
    assert it.log == [-1.0, 5.0]
    assert it.get(A, 0) == -1.0


def test_directed_all_dead_stores():
    # Whole body is dead stores; only the log is observable.
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(_sc("a"), IRConst(1)),
                IRSet(_sc("b"), IRPureInstr(Op.Add, [IRConst(2), IRConst(3)])),
                IRSet(_sc("c"), _rd("a")),
                _log(42),
                IRSet(_sc("d"), IRConst(9)),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    it, _ = _assert_levels_agree(build)
    assert it.log == [42.0]
    assert all(it.get(A, i) == -1.0 for i in range(OBS_CAPTURE_LEN))


def test_directed_deep_chain_stack_safety():
    # ~200 sequential blocks -- emission/traversal/interpretation stack safety.
    n = 200

    def build():
        blocks = [BasicBlock(statements=[_log(i)]) for i in range(n)]
        for i in range(n - 1):
            blocks[i].connect_to(blocks[i + 1], None)
        return blocks[0]

    it, _ = _assert_levels_agree(build)
    assert it.log == [float(i) for i in range(n)]


def test_directed_swap_loop():
    # a, b = b, a repeated -- copy chains that will stress phi cycles once SSA
    # lands. Three swaps of (1, 2) -> (2, 1).
    def build():
        init = BasicBlock(
            statements=[IRSet(_sc("a"), IRConst(1)), IRSet(_sc("b"), IRConst(2)), IRSet(_sc("k"), IRConst(0))]
        )
        header = BasicBlock(test=IRPureInstr(Op.Less, [_rd("k"), IRConst(3)]))
        body = BasicBlock(
            statements=[IRSet(_sc("t"), _rd("a")), IRSet(_sc("a"), _rd("b")), IRSet(_sc("b"), _rd("t"))]
        )
        step = BasicBlock(statements=[IRSet(_sc("k"), IRPureInstr(Op.Add, [_rd("k"), IRConst(1)]))])
        after = BasicBlock(statements=[_log(_rd("a")), _log(_rd("b")), _obs(A, 0, _rd("a")), _obs(B, 0, _rd("b"))])
        init.connect_to(header, None)
        header.connect_to(body, None)
        header.connect_to(after, 0)
        body.connect_to(step, None)
        step.connect_to(header, None)
        return init

    it, _ = _assert_levels_agree(build)
    assert it.log == [2.0, 1.0]
    assert it.get(A, 0) == 2.0
    assert it.get(B, 0) == 1.0


def test_directed_pointer_deref():
    # BlockPlace(block=BlockPlace(...)) -- a block id read from memory we control
    # (section 3: BlockPlace.block may be a place; finalize emits nested Gets).
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(30, 3, 0), IRConst(31)),  # mem[30][3] = block id 31
                IRSet(BlockPlace(31, 5, 0), IRConst(42)),  # mem[31][5] = 42
                _log(IRGet(BlockPlace(BlockPlace(30, 3, 0), 5, 0))),  # Get(Get(30,3),5) -> 42
                IRSet(BlockPlace(30, 0, 0), IRGet(BlockPlace(BlockPlace(30, 3, 0), 5, 0))),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    it, _ = _assert_levels_agree(build, blocks=(30, 31))
    assert it.log == [42.0]
    assert it.get(30, 0) == 42.0


def test_directed_dynamic_array_index_in_loop():
    # arr[counter % size] written and read with a dynamic (computed) index.
    def build():
        arr = TempBlock("arr", 3)
        init = BasicBlock(
            statements=[IRSet(BlockPlace(arr, k), IRConst(0)) for k in range(3)] + [IRSet(_sc("k"), IRConst(0))]
        )
        header = BasicBlock(test=IRPureInstr(Op.Less, [_rd("k"), IRConst(3)]))
        idx = IRPureInstr(Op.Mod, [_rd("k"), IRConst(3)])
        body = BasicBlock(statements=[IRSet(BlockPlace(arr, idx), IRPureInstr(Op.Add, [_rd("k"), IRConst(10)]))])
        step = BasicBlock(statements=[IRSet(_sc("k"), IRPureInstr(Op.Add, [_rd("k"), IRConst(1)]))])
        after = BasicBlock(
            statements=[
                _obs(A, 0, IRGet(BlockPlace(arr, 0))),
                _obs(A, 1, IRGet(BlockPlace(arr, 1))),
                _obs(A, 2, IRGet(BlockPlace(arr, 2))),
            ]
        )
        init.connect_to(header, None)
        header.connect_to(body, None)
        header.connect_to(after, 0)
        body.connect_to(step, None)
        step.connect_to(header, None)
        return init

    it, _ = _assert_levels_agree(build)
    assert [it.get(A, i) for i in range(3)] == [10.0, 11.0, 12.0]


def test_directed_noncontiguous_switch_with_default():
    # Non-contiguous case labels {0, 3, 5} with a default -> SwitchWithDefault.
    def build_for(sel):
        def build():
            a = BasicBlock(statements=[_obs(A, 0, sel)], test=IRGet(BlockPlace(A, 0)))
            join = BasicBlock(statements=[_obs(B, 0, 1)])
            for cond, logv in ((0, 100), (3, 103), (5, 105)):
                blk = BasicBlock(statements=[_log(logv)])
                a.connect_to(blk, cond)
                blk.connect_to(join, None)
            d = BasicBlock(statements=[_log(999)])
            a.connect_to(d, None)
            d.connect_to(join, None)
            return a

        return build

    for sel, expected in ((0, [100.0]), (3, [103.0]), (5, [105.0]), (4, [999.0])):
        it, _ = _assert_levels_agree(build_for(sel))
        assert it.log == expected


# ==========================================================================
# Pinned real M1 bug (found by this suite).
# ==========================================================================


@pytest.mark.xfail(
    reason=(
        "M1 allocation bug: STANDARD's first-fit interference packing treats a "
        "never-written (but read) size>1 array temp as dead -- arrays are 'not "
        "live before any write' (section 7.5) and there is no write -- so it "
        "overlaps the array's base slot onto a live scalar. The undefined array "
        "read then aliases that scalar instead of yielding the -1.0 padding, so "
        "STANDARD diverges from MINIMAL/FAST (bump/try-bump keep them separate). "
        "This is the 'undefined read assumption violated' case called out in the "
        "task; fix belongs in liveness/interference for never-written arrays."
    ),
    strict=True,
)
def test_never_written_array_read_aliases_live_temp():
    # arr0 is never written but arr0[0] is read; k0 is a live loop counter. Under
    # STANDARD packing, arr0 base collides with k0, so DebugLog(arr0[0]) logs k0's
    # value (0) instead of -1.0.
    def build():
        arr = TempBlock("arr0", 2)
        b0 = BasicBlock(statements=[IRSet(_sc("k0"), IRConst(0))])
        header = BasicBlock(test=IRPureInstr(Op.Less, [_rd("k0"), IRConst(1)]))
        body = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRGet(BlockPlace(arr, 0))])])
        step = BasicBlock(statements=[IRSet(_sc("k0"), IRPureInstr(Op.Add, [_rd("k0"), IRConst(1)]))])
        after = BasicBlock()
        b0.connect_to(header, None)
        header.connect_to(body, None)
        header.connect_to(after, 0)
        body.connect_to(step, None)
        step.connect_to(header, None)
        return b0

    it, _ = _assert_levels_agree(build)
    assert it.log == [-1.0]
