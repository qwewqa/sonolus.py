"""If-conversion tests (milestone M3, OPTIMIZER_REWRITE.md 7.3).

Three layers, mirroring test_lower.py: semantic + structural units, the pydori
corpus, and random-CFG semantic parity.

* Units build a diamond/triangle/{VALUE C, NONE} head, run it through
  ``run_ifconv`` (post-mid-end SSA) / ``run_ifconv_full`` (the whole
  standard-style path + emit), and assert the fold decision, the emitted
  ``If``/``Equal`` node shape + arm order, the strict arm legality, the arm
  budget, phi collapse, and the must-fold invariant -- proven end-to-end by a
  guarded division that must NOT fault and must match the non-if-converted result.
* Corpus: every pydori callback through midend -> if_convert -> lower_from_ssa ->
  allocate -> emit, with verify()-green SSA, a conversion count, and an
  effective-node-count delta vs the no-if-conversion baseline.
* Parity: the random-CFG property recipes through the if-conversion path vs the
  MINIMAL reference (observables equal).
"""

from __future__ import annotations

import struct

import pytest
from hypothesis import HealthCheck, given, settings

from sonolus.backend._opt import ir, lower  # noqa: PLC2701
from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import MINIMAL_PASSES, OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text
from sonolus.backend.place import BlockPlace, TempBlock
from tests.backend._cfg_gen import OBS_BLOCKS, OBS_CAPTURE_LEN, build_cfg, programs
from tests.backend.test_corpus_roundtrip import _MODE_SETUP, _iter_callbacks

_ROM = [float("nan"), float("inf"), -float("inf")]

# Input/output memory blocks for the hand-built diamonds (plain ints -> writable
# under mode=None, so genuine runtime values that never fold to constants).
_IN = 20
_OUT0, _OUT1, _OUT2 = 21, 22, 23


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _sc(name: str) -> BlockPlace:
    return BlockPlace(TempBlock(name, 1), 0, 0)


def _rd(name: str) -> IRGet:
    return IRGet(_sc(name))


def _in(idx: int) -> IRGet:
    return IRGet(BlockPlace(_IN, idx))


def _interp(node, mem: dict | None = None) -> Interpreter:
    it = Interpreter()
    it.blocks[3000] = list(_ROM)
    for block, values in (mem or {}).items():
        it.blocks[block] = list(values)
    it.run(node)
    return it


def _count_conversions(build, mode=None, cb=None) -> int:
    return lower.run_ifconv_counted(build(), mode, cb)[1]


def _find_nodes(node, op: Op, out: list | None = None) -> list:
    out = [] if out is None else out
    if isinstance(node, FunctionNode):
        if node.func == op:
            out.append(node)
        for arg in node.args:
            _find_nodes(arg, op, out)
    return out


def _two_use_join(r_true, r_false, *, test=None, head_stmts=None):
    # A diamond whose 2-statement join uses the merged value ``r`` twice, so
    # cfg_cleanup does not tail-duplicate/dissolve it, leaving a real join phi.
    test = test if test is not None else _in(3)
    head = BasicBlock(statements=list(head_stmts or [IRSet(_sc("a"), _in(0))]), test=test)
    t = BasicBlock(statements=[IRSet(_sc("r"), r_true)])
    f = BasicBlock(statements=[IRSet(_sc("r"), r_false)])
    j = BasicBlock(
        statements=[
            IRSet(BlockPlace(_OUT0, 0), _rd("r")),
            IRSet(BlockPlace(_OUT1, 0), IRPureInstr(Op.Add, [_rd("r"), IRConst(1)])),
        ]
    )
    head.connect_to(f, 0)
    head.connect_to(t, None)
    t.connect_to(j, None)
    f.connect_to(j, None)
    return head


# ---------------------------------------------------------------------------
# THE key semantic test: a guarded division must fold UNDER the If (7.3).
# ---------------------------------------------------------------------------


def _divide_diamond():
    # r = a / b if b != 0 else 0 ; the true arm is Divide(a, b) with a, b read in
    # the head (above the arm). Two observable stores keep the join alive.
    head = BasicBlock(
        statements=[IRSet(_sc("a"), _in(0)), IRSet(_sc("b"), _in(1))],
        test=IRPureInstr(Op.NotEqual, [_rd("b"), IRConst(0)]),
    )
    t = BasicBlock(statements=[IRSet(_sc("r"), IRPureInstr(Op.Divide, [_rd("a"), _rd("b")]))])
    f = BasicBlock(statements=[IRSet(_sc("r"), IRConst(0))])
    j = BasicBlock(
        statements=[
            IRSet(BlockPlace(_OUT0, 0), _rd("r")),
            IRSet(BlockPlace(_OUT1, 0), IRPureInstr(Op.Add, [_rd("r"), IRConst(1)])),
        ]
    )
    head.connect_to(f, 0)
    head.connect_to(t, None)
    t.connect_to(j, None)
    f.connect_to(j, None)
    return head


def test_guarded_division_folds_under_if_no_fault():
    # 1) if-conversion fires exactly once and the Divide folds INTO the If arm
    #    (not materialised to a temp): the emitted If's TRUE arm is a Divide node.
    assert _count_conversions(_divide_diamond) == 1
    node = cfg_to_engine_node(lower.run_ifconv_full(_divide_diamond()))
    ifs = _find_nodes(node, Op.If)
    assert len(ifs) == 1, cfg_to_text(lower.run_ifconv_full(_divide_diamond()))
    _test_node, t_arm, _f_arm = ifs[0].args
    assert isinstance(t_arm, FunctionNode)
    assert t_arm.func == Op.Divide, "the guarded Divide must FOLD under the If's true arm (must-fold)"

    # 2) with b == 0 the runtime/oracle only evaluates the taken (false) arm, so
    #    NO ZeroDivisionError and the result is 0 -- the whole point of must-fold.
    for b in (0.0, 3.0):
        it = _interp(node, {_IN: [6.0, b]})
        expect = 0.0 if b == 0.0 else 2.0
        assert it.get(_OUT0, 0) == expect
        assert it.get(_OUT1, 0) == expect + 1

    # 3) the non-if-converted M2 path (a real branch) agrees. Its only If is the
    #    block-dispatch TERMINATOR (integer arm indices), never a select folding
    #    the Divide -- the Divide lives in its own Execute block there.
    base = cfg_to_engine_node(lower.run_lower(_divide_diamond(), midend=True))
    for if_node in _find_nodes(base, Op.If):
        for arg in if_node.args:
            assert not (isinstance(arg, FunctionNode) and arg.func == Op.Divide)
    for b in (0.0, 3.0):
        itc = _interp(node, {_IN: [6.0, b]})
        itb = _interp(base, {_IN: [6.0, b]})
        assert itc.get(_OUT0, 0) == itb.get(_OUT0, 0)
        assert itc.get(_OUT1, 0) == itb.get(_OUT1, 0)


# ---------------------------------------------------------------------------
# Structural units: shapes + arm order.
# ---------------------------------------------------------------------------


def test_diamond_to_if_arm_order():
    # {VALUE 0 -> F, NONE -> T}: If(test, TRUE=none-side, FALSE=zero-side).
    def build():
        return _two_use_join(IRPureInstr(Op.Abs, [_rd("a")]), IRPureInstr(Op.Negate, [_rd("a")]))

    assert _count_conversions(build) == 1
    node = cfg_to_engine_node(lower.run_ifconv_full(build()))
    ifs = _find_nodes(node, Op.If)
    assert len(ifs) == 1
    _test, t_arm, f_arm = ifs[0].args
    assert isinstance(t_arm, FunctionNode)
    assert t_arm.func == Op.Abs  # NONE / true side
    assert isinstance(f_arm, FunctionNode)
    assert f_arm.func == Op.Negate  # VALUE 0 / false side


def _triangle(true_direct: bool):
    # One side is a direct head->join edge (value is head-available), the other a block.
    head = BasicBlock(
        statements=[IRSet(_sc("a"), _in(0)), IRSet(_sc("d"), IRPureInstr(Op.Abs, [_in(2)]))], test=_in(3)
    )
    arm = BasicBlock(statements=[IRSet(_sc("r"), IRPureInstr(Op.Negate, [_rd("a")]))])
    j = BasicBlock(
        statements=[
            IRSet(BlockPlace(_OUT0, 0), _rd("r")),
            IRSet(BlockPlace(_OUT1, 0), IRPureInstr(Op.Add, [_rd("r"), IRConst(1)])),
        ]
    )
    # Direct side sets r in the head to the head-available value d.
    head.statements.append(IRSet(_sc("r"), _rd("d")))
    if true_direct:
        head.connect_to(arm, 0)  # VALUE 0 (false) -> arm
        head.connect_to(j, None)  # NONE (true) -> j direct
    else:
        head.connect_to(j, 0)  # VALUE 0 (false) -> j direct
        head.connect_to(arm, None)  # NONE (true) -> arm
    arm.connect_to(j, None)
    return head


def test_triangle_true_direct():
    assert _count_conversions(lambda: _triangle(True)) == 1
    node = cfg_to_engine_node(lower.run_ifconv_full(_triangle(True)))
    ifs = _find_nodes(node, Op.If)
    assert len(ifs) == 1
    _test, t_arm, f_arm = ifs[0].args
    assert isinstance(t_arm, FunctionNode)
    assert t_arm.func == Op.Abs  # direct head-available on NONE/true
    assert isinstance(f_arm, FunctionNode)
    assert f_arm.func == Op.Negate  # arm on VALUE 0/false


def test_triangle_false_direct():
    assert _count_conversions(lambda: _triangle(False)) == 1
    node = cfg_to_engine_node(lower.run_ifconv_full(_triangle(False)))
    ifs = _find_nodes(node, Op.If)
    assert len(ifs) == 1
    _test, t_arm, f_arm = ifs[0].args
    assert isinstance(t_arm, FunctionNode)
    assert t_arm.func == Op.Negate  # arm on NONE/true
    assert isinstance(f_arm, FunctionNode)
    assert f_arm.func == Op.Abs  # direct on VALUE 0/false


def _equal_form_diamond():
    # {VALUE 5 -> c-arm, NONE -> default}: If(Equal(test, 5), c-arm, default).
    head = BasicBlock(statements=[IRSet(_sc("a"), _in(0))], test=_in(2))
    t = BasicBlock(statements=[IRSet(_sc("r"), IRPureInstr(Op.Abs, [_rd("a")]))])
    f = BasicBlock(statements=[IRSet(_sc("r"), IRConst(9))])
    j = BasicBlock(
        statements=[
            IRSet(BlockPlace(_OUT0, 0), _rd("r")),
            IRSet(BlockPlace(_OUT1, 0), IRPureInstr(Op.Add, [_rd("r"), IRConst(1)])),
        ]
    )
    head.connect_to(t, 5)  # VALUE 5 -> c side (true arm)
    head.connect_to(f, None)  # NONE -> default (false arm)
    t.connect_to(j, None)
    f.connect_to(j, None)
    return head


def test_equal_form_c_none():
    assert _count_conversions(_equal_form_diamond) == 1
    node = cfg_to_engine_node(lower.run_ifconv_full(_equal_form_diamond()))
    ifs = _find_nodes(node, Op.If)
    assert len(ifs) == 1
    test_node, t_arm, f_arm = ifs[0].args
    assert isinstance(test_node, FunctionNode)
    assert test_node.func == Op.Equal
    assert test_node.args[1] == 5  # Equal(test, 5)
    assert isinstance(t_arm, FunctionNode)
    assert t_arm.func == Op.Abs  # C side is the true arm
    assert f_arm == 9  # default is the false arm

    for x, a in ((5, -4.0), (2, -4.0)):
        it = _interp(node, {_IN: [a, 0.0, float(x)]})
        assert it.get(_OUT0, 0) == (abs(a) if x == 5 else 9)


# ---------------------------------------------------------------------------
# Arm budget (per select arm tree, effective §2 cost <= 8).
# ---------------------------------------------------------------------------


def _head_ab():
    # Read a, b into the head (above the arm) so the arm holds no OPX_GET.
    return [IRSet(_sc("a"), _in(0)), IRSet(_sc("b"), _in(1))]


def test_arm_budget_boundary():
    # cost 8: Multiply(a, Negate(b)) = 1 + 3(a) + (1+3)(Negate b) = 8 -> converts.
    # cost 9: Multiply(Negate(a), Negate(b)) = 1 + 4 + 4 = 9 -> does NOT convert.
    # (Multiply avoids the GVN Subtract/Add<->Negate recombination (finding #6),
    # which would otherwise fold Subtract(x, Negate(y)) -> Add(x, y) pre-ifconv.)
    def c8():
        return _two_use_join(
            IRPureInstr(Op.Multiply, [_rd("a"), IRPureInstr(Op.Negate, [_rd("b")])]),
            IRConst(0),
            head_stmts=_head_ab(),
        )

    def c9():
        return _two_use_join(
            IRPureInstr(Op.Multiply, [IRPureInstr(Op.Negate, [_rd("a")]), IRPureInstr(Op.Negate, [_rd("b")])]),
            IRConst(0),
            head_stmts=_head_ab(),
        )

    assert _count_conversions(c8) == 1
    assert _count_conversions(c9) == 0


def test_runtime_constant_arm_huge_but_effective_cost_1():
    # A huge pure arithmetic tree over CONSTANTS folds (SCCP) to a single constant
    # -- effective cost 1 -- and converts regardless of raw size; the same-shaped
    # tree over a head-available runtime read blows the budget and does not.
    def chain(op, leaf, n):
        e = leaf
        for _ in range(n):
            e = IRPureInstr(op, [e, IRConst(1)])
        return e

    assert _count_conversions(lambda: _two_use_join(chain(Op.Add, IRConst(0), 40), IRConst(0))) == 1
    assert _count_conversions(lambda: _two_use_join(chain(Op.Subtract, _rd("a"), 40), IRConst(0))) == 0


# ---------------------------------------------------------------------------
# Arm legality (strict): reads / Random / stores / multi-use all block it.
# ---------------------------------------------------------------------------


def test_arm_with_memory_read_does_not_convert():
    # A writable-block read in the arm is an OPX_GET -> illegal (would speculate a load).
    assert _count_conversions(lambda: _two_use_join(_in(5), IRConst(0))) == 0


def test_arm_with_runtime_constant_read_does_not_convert():
    # Even an EngineRom (runtime-constant) read is an OPX_GET -> illegal in an arm.
    def build():
        head = BasicBlock(statements=[IRSet(_sc("a"), _in(0))], test=_in(3))
        t = BasicBlock(statements=[IRSet(_sc("r"), IRGet(BlockPlace(PlayBlock.EngineRom, 0)))])
        f = BasicBlock(statements=[IRSet(_sc("r"), IRConst(0))])
        j = BasicBlock(
            statements=[
                IRSet(BlockPlace(_OUT0, 0), _rd("r")),
                IRSet(BlockPlace(_OUT1, 0), IRPureInstr(Op.Add, [_rd("r"), IRConst(1)])),
            ]
        )
        head.connect_to(f, 0)
        head.connect_to(t, None)
        t.connect_to(j, None)
        f.connect_to(j, None)
        return head

    assert lower.run_ifconv_counted(build(), Mode.PLAY, "updateSequential")[1] == 0


def test_arm_with_random_does_not_convert():
    assert _count_conversions(lambda: _two_use_join(IRInstr(Op.Random, [IRConst(0), _rd("a")]), IRConst(0))) == 0


def test_arm_with_store_does_not_convert():
    def build():
        head = BasicBlock(statements=[IRSet(_sc("a"), _in(0))], test=_in(3))
        t = BasicBlock(statements=[IRSet(BlockPlace(_OUT2, 5), IRConst(7)), IRSet(_sc("r"), _rd("a"))])
        f = BasicBlock(statements=[IRSet(_sc("r"), IRConst(0))])
        j = BasicBlock(
            statements=[
                IRSet(BlockPlace(_OUT0, 0), _rd("r")),
                IRSet(BlockPlace(_OUT1, 0), IRPureInstr(Op.Add, [_rd("r"), IRConst(1)])),
            ]
        )
        head.connect_to(f, 0)
        head.connect_to(t, None)
        t.connect_to(j, None)
        f.connect_to(j, None)
        return head

    assert _count_conversions(build) == 0


def test_arm_value_used_beyond_the_select_does_not_convert():
    # A shared arm subexpression (m used twice) is a DAG, not a single-use tree:
    # folding it would duplicate/materialise -> conversion is illegal.
    def build():
        head = BasicBlock(statements=[IRSet(_sc("a"), _in(0))], test=_in(3))
        t = BasicBlock(
            statements=[
                IRSet(_sc("m"), IRPureInstr(Op.Abs, [_rd("a")])),
                IRSet(_sc("r"), IRPureInstr(Op.Add, [_rd("m"), _rd("m")])),
            ]
        )
        f = BasicBlock(statements=[IRSet(_sc("r"), IRConst(0))])
        j = BasicBlock(
            statements=[
                IRSet(BlockPlace(_OUT0, 0), _rd("r")),
                IRSet(BlockPlace(_OUT1, 0), IRPureInstr(Op.Add, [_rd("r"), IRConst(1)])),
            ]
        )
        head.connect_to(f, 0)
        head.connect_to(t, None)
        t.connect_to(j, None)
        f.connect_to(j, None)
        return head

    assert _count_conversions(build) == 0


# ---------------------------------------------------------------------------
# Multiple phis, phi collapse, additional join predecessors.
# ---------------------------------------------------------------------------


def _multi_phi_diamond():
    head = BasicBlock(statements=[IRSet(_sc("a"), _in(0))], test=_in(3))
    t = BasicBlock(
        statements=[
            IRSet(_sc("r"), IRPureInstr(Op.Abs, [_rd("a")])),
            IRSet(_sc("s"), IRPureInstr(Op.Negate, [_rd("a")])),
        ]
    )
    f = BasicBlock(statements=[IRSet(_sc("r"), IRConst(0)), IRSet(_sc("s"), IRConst(1))])
    j = BasicBlock(
        statements=[
            IRSet(BlockPlace(_OUT0, 0), _rd("r")),
            IRSet(BlockPlace(_OUT1, 0), _rd("s")),
            IRSet(BlockPlace(_OUT2, 0), IRPureInstr(Op.Add, [_rd("r"), _rd("s")])),
        ]
    )
    head.connect_to(f, 0)
    head.connect_to(t, None)
    t.connect_to(j, None)
    f.connect_to(j, None)
    return head


def test_multiple_phis_share_test_distinct_selects():
    assert _count_conversions(_multi_phi_diamond) == 1
    node = cfg_to_engine_node(lower.run_ifconv_full(_multi_phi_diamond()))
    ifs = _find_nodes(node, Op.If)
    # Two distinct selects, one per phi, sharing the SAME test node object (consed).
    assert len(ifs) == 2
    assert ifs[0].args[0] is ifs[1].args[0]

    for x in (0.0, 1.0):  # test falsy / truthy
        it = _interp(node, {_IN: [-4.0, 0.0, 0.0, x]})
        r = abs(-4.0) if x != 0.0 else 0.0
        s = 4.0 if x != 0.0 else 1.0
        assert it.get(_OUT0, 0) == r
        assert it.get(_OUT1, 0) == s
        assert it.get(_OUT2, 0) == r + s


def test_phi_collapses_when_only_two_converted_preds():
    # The join's only predecessors are the two converted arms -> the phi collapses
    # to the select (no phi survives in the exported CFG).
    cfg = lower.run_ifconv(_two_use_join(IRPureInstr(Op.Abs, [_rd("a")]), IRConst(0)))
    text = cfg_to_text(cfg)
    assert "phi(" not in text, text
    assert "If(" in text, text


def _extra_pred_diamond():
    # A loop back edge gives the join a third predecessor: the diamond still
    # converts, but the phi survives (the select replaces the two converted
    # operands; the latch operand stays).
    pre = BasicBlock(statements=[IRSet(_sc("a"), _in(0)), IRSet(_sc("i"), IRConst(0))])
    head = BasicBlock(test=_in(3))
    t = BasicBlock(statements=[IRSet(_sc("r"), IRPureInstr(Op.Abs, [_rd("a")]))])
    f = BasicBlock(statements=[IRSet(_sc("r"), IRPureInstr(Op.Negate, [_rd("a")]))])
    loop = BasicBlock(test=IRPureInstr(Op.Less, [_rd("i"), IRConst(3)]))  # loop header == join
    body = BasicBlock(
        statements=[
            IRSet(BlockPlace(_OUT0, 0), _rd("r")),
            IRSet(_sc("i"), IRPureInstr(Op.Add, [_rd("i"), IRConst(1)])),
            IRSet(_sc("r"), IRPureInstr(Op.Add, [_rd("r"), IRConst(1)])),
        ]
    )
    ex = BasicBlock(statements=[IRSet(BlockPlace(_OUT1, 0), _rd("r"))])
    pre.connect_to(head, None)
    head.connect_to(f, 0)
    head.connect_to(t, None)
    t.connect_to(loop, None)
    f.connect_to(loop, None)
    loop.connect_to(body, None)
    loop.connect_to(ex, 0)
    body.connect_to(loop, None)  # back edge -> 3rd pred of the join
    return pre


def test_join_with_extra_pred_keeps_phi():
    assert _count_conversions(_extra_pred_diamond) == 1
    text = cfg_to_text(lower.run_ifconv(_extra_pred_diamond()))
    assert "If(" in text, text  # a select was created
    assert "phi(" in text, text  # the loop-header phi survives


# ---------------------------------------------------------------------------
# Nested diamonds (fixpoint) + multiway deferral.
# ---------------------------------------------------------------------------


def _nested_diamond():
    # x = c1 ? a : (c2 ? 7 : 5) -- shared join; inner converts, then the outer
    # folds the inner If as its arm. Two conversions across the single-pass fixpoint.
    head = BasicBlock(
        statements=[IRSet(_sc("a"), _in(0)), IRSet(_sc("c1"), _in(3)), IRSet(_sc("c2"), _in(4))],
        test=_rd("c1"),
    )
    outer_true = BasicBlock(statements=[IRSet(_sc("x"), _rd("a"))])
    inner = BasicBlock(test=_rd("c2"))
    inner_true = BasicBlock(statements=[IRSet(_sc("x"), IRConst(7))])
    inner_false = BasicBlock(statements=[IRSet(_sc("x"), IRConst(5))])
    j = BasicBlock(
        statements=[
            IRSet(BlockPlace(_OUT0, 0), _rd("x")),
            IRSet(BlockPlace(_OUT1, 0), IRPureInstr(Op.Add, [_rd("x"), IRConst(1)])),
        ]
    )
    head.connect_to(inner, 0)
    head.connect_to(outer_true, None)
    inner.connect_to(inner_false, 0)
    inner.connect_to(inner_true, None)
    outer_true.connect_to(j, None)
    inner_true.connect_to(j, None)
    inner_false.connect_to(j, None)
    return head


def test_nested_diamonds_reach_fixpoint():
    assert _count_conversions(_nested_diamond) == 2
    node = cfg_to_engine_node(lower.run_ifconv_full(_nested_diamond()))
    ifs = _find_nodes(node, Op.If)
    assert len(ifs) >= 2  # outer If with the inner If nested in an arm
    for c1 in (0.0, 1.0):
        for c2 in (0.0, 1.0):
            it = _interp(node, {_IN: [4.0, 0.0, 0.0, c1, c2]})
            expect = 4.0 if c1 != 0.0 else (7.0 if c2 != 0.0 else 5.0)
            assert it.get(_OUT0, 0) == expect


def _multiway_block():
    # A >=3-way switch head is not a two-way head: DEFERRED (documented). It must
    # be left intact (no conversion, no crash, verify()/emit still fine).
    head = BasicBlock(statements=[IRSet(_sc("a"), _in(0))], test=_in(3))
    b0 = BasicBlock(statements=[IRSet(_sc("r"), IRPureInstr(Op.Abs, [_rd("a")]))])
    b1 = BasicBlock(statements=[IRSet(_sc("r"), IRPureInstr(Op.Negate, [_rd("a")]))])
    b2 = BasicBlock(statements=[IRSet(_sc("r"), IRConst(2))])
    dflt = BasicBlock(statements=[IRSet(_sc("r"), IRConst(9))])
    j = BasicBlock(
        statements=[
            IRSet(BlockPlace(_OUT0, 0), _rd("r")),
            IRSet(BlockPlace(_OUT1, 0), IRPureInstr(Op.Add, [_rd("r"), IRConst(1)])),
        ]
    )
    head.connect_to(b0, 0)
    head.connect_to(b1, 1)
    head.connect_to(b2, 2)
    head.connect_to(dflt, None)
    for b in (b0, b1, b2, dflt):
        b.connect_to(j, None)
    return head


def test_multiway_switch_not_converted_deferred():
    assert _count_conversions(_multiway_block) == 0
    node = cfg_to_engine_node(lower.run_ifconv_full(_multiway_block()))
    assert isinstance(node, FunctionNode)  # still lowers + emits fine


# ---------------------------------------------------------------------------
# Corpus: every pydori callback through the if-conversion path.
# ---------------------------------------------------------------------------


def _count_nodes(node) -> int:
    if isinstance(node, FunctionNode):
        return 1 + sum(_count_nodes(a) for a in node.args)
    return 1


def _rc_block_ids(mode: Mode) -> set:
    return {int(m) for m in mode.blocks if m.name in ir.RUNTIME_CONSTANT_BLOCKS}


def _is_rc(node, rcids: set) -> bool:
    if not isinstance(node, FunctionNode):
        return True
    if node.func == Op.Get:
        blk, idx = node.args
        return isinstance(blk, int) and blk in rcids and _is_rc(idx, rcids)
    if node.func.pure and not node.func.side_effects:
        return all(_is_rc(a, rcids) for a in node.args)
    return False


def _eff_nodes(node, rcids: set) -> int:
    if not isinstance(node, FunctionNode):
        return 1
    if _is_rc(node, rcids):
        return 1
    return 1 + sum(_eff_nodes(a, rcids) for a in node.args)


@pytest.mark.parametrize("mode", list(_MODE_SETUP))
def test_corpus_if_convert(mode: Mode):
    rcids = _rc_block_ids(mode)
    conversions = converted_cbs = count = 0
    eff_base = eff_conv = raw_base = raw_conv = 0
    for _label, cbname, factory in _iter_callbacks(mode):
        # if_convert output must be verify()-green SSA (run_ifconv asserts it).
        _cfg, n = lower.run_ifconv_counted(factory(), mode, cbname)
        if n:
            conversions += n
            converted_cbs += 1
        # full standard-style path emits successfully.
        node = cfg_to_engine_node(lower.run_ifconv_full(factory(), mode, cbname))
        assert isinstance(node, FunctionNode)
        base = cfg_to_engine_node(lower.run_lower(factory(), mode, cbname, midend=True))
        raw_conv += _count_nodes(node)
        raw_base += _count_nodes(base)
        eff_conv += _eff_nodes(node, rcids)
        eff_base += _eff_nodes(base, rcids)
        count += 1
    assert count > 0
    # If-conversion must not regress effective node counts on the corpus.
    assert eff_conv <= eff_base, f"[{mode.name}] if-conversion regressed effective nodes"
    print(
        f"\n[{mode.name}] conversions={conversions} over {converted_cbs}/{count} callbacks | "
        f"raw base={raw_base} conv={raw_conv} ({100.0 * (raw_conv - raw_base) / raw_base:+.2f}%) | "
        f"eff base={eff_base} conv={eff_conv} ({100.0 * (eff_conv - eff_base) / eff_base:+.2f}%)"
    )


# ---------------------------------------------------------------------------
# Semantic parity: random CFG recipes through the if-conversion path.
# ---------------------------------------------------------------------------


def _f(x: float) -> bytes:
    # Compare +0.0 and -0.0 as equal: the mid-end legitimately collapses the sign
    # of zero relative to the MINIMAL reference via the documented policy exceptions
    # (OPTIMIZER_REWRITE.md 7.2.2 / 4 -- e.g. SCCP folds ``Negate(Not(k))`` to +0.0
    # where the unoptimized tree evaluates to -0.0). Mirrors test_random_cfg.py's _f;
    # NaN bytes pass through unchanged (NaN != 0.0), so NaN drift is still caught.
    x = float(x)
    if x == 0.0:
        x = 0.0
    return struct.pack(">d", x)


def _observe(it: Interpreter) -> tuple:
    key = [b"log", *(_f(x) for x in it.log), b"mem"]
    for block in OBS_BLOCKS:
        key.extend(_f(it.get(block, i)) for i in range(OBS_CAPTURE_LEN))
    return tuple(key)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(program=programs())
def test_random_programs_match_reference(program):
    def build():
        return build_cfg(program)

    ref = _interp(cfg_to_engine_node(run_passes(build(), MINIMAL_PASSES, OptimizerConfig())))
    conv = _interp(cfg_to_engine_node(lower.run_ifconv_full(build())))
    assert _observe(ref) == _observe(conv)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(program=programs(max_depth=4))
def test_random_deeper_programs_match_reference(program):
    def build():
        return build_cfg(program)

    ref = _interp(cfg_to_engine_node(run_passes(build(), MINIMAL_PASSES, OptimizerConfig())))
    conv = _interp(cfg_to_engine_node(lower.run_ifconv_full(build())))
    assert _observe(ref) == _observe(conv)
