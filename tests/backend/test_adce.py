"""Control-dependent DCE and conservative cycle termination tests."""

import pytest

from sonolus.backend._opt import ir  # ruff: ignore[import-private-name]
from sonolus.backend.ir import IRConst, IRInstr, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text, traverse_cfg_reverse_postorder
from tests.backend.test_midend import _assert_semantics, _log, _rd, _sc, _w

_PHASES = ["ssa", "gvn", "dce"]


def _optimized(build, *, cleanup=False):
    return ir.debug_run(build(), phases=(["cfg_cleanup"] if cleanup else []) + _PHASES)


def _tests(cfg):
    return [
        block.test
        for block in traverse_cfg_reverse_postorder(cfg)
        if any(edge.cond is not None for edge in block.outgoing)
    ]


def test_dead_nested_conditions_and_phi_disappear():
    def build():
        entry = BasicBlock(test=_w(0))
        nested = BasicBlock(test=_w(1))
        a = BasicBlock(statements=[IRSet(_sc("unused"), _w(2))])
        b = BasicBlock(statements=[IRSet(_sc("unused"), _w(3))])
        join = BasicBlock(statements=[_log(42)])
        entry.connect_to(nested, 0)
        entry.connect_to(join, None)
        nested.connect_to(a, 0)
        nested.connect_to(b, None)
        a.connect_to(join, None)
        b.connect_to(join, None)
        return entry

    assert not _tests(_optimized(build))
    assert _assert_semantics(build).log == [42]


@pytest.mark.parametrize("selector", [0, 1])
def test_live_phi_keeps_edge_selection(selector):
    def build():
        entry = BasicBlock(statements=[IRSet(_sc("selector"), _w(0))], test=_rd("selector"))
        a = BasicBlock(statements=[IRSet(_sc("value"), IRConst(10))])
        b = BasicBlock(statements=[IRSet(_sc("value"), IRConst(20))])
        join = BasicBlock(statements=[_log(_rd("value"))])
        entry.connect_to(a, 0)
        entry.connect_to(b, None)
        a.connect_to(join, None)
        b.connect_to(join, None)
        init = BasicBlock(statements=[IRSet(_w(0).place, IRConst(selector))])
        init.connect_to(entry, None)
        return init

    assert _tests(_optimized(build))
    assert _assert_semantics(build).log == [10 if selector == 0 else 20]


@pytest.mark.parametrize("selector", [0, 1, 2])
def test_defaultless_switch_preserves_implicit_exit(selector):
    def build():
        entry = BasicBlock(statements=[IRSet(_w(0).place, IRConst(selector))], test=_w(0))
        a = BasicBlock(statements=[_log(10)])
        b = BasicBlock(statements=[_log(20)])
        entry.connect_to(a, 0)
        entry.connect_to(b, 1)
        return entry

    assert _tests(_optimized(build))
    assert _assert_semantics(build).log == ([10] if selector == 0 else [20] if selector == 1 else [])


def test_dead_defaultless_switch_can_exit_directly():
    def build():
        entry = BasicBlock(test=_w(0))
        a = BasicBlock(statements=[IRSet(_sc("unused"), _w(1))])
        b = BasicBlock(statements=[IRSet(_sc("unused"), _w(2))])
        entry.connect_to(a, 0)
        entry.connect_to(b, 1)
        return entry

    assert not _tests(_optimized(build))
    assert _assert_semantics(build).log == []


def test_dead_branch_preserves_effect_in_test():
    def build():
        entry = BasicBlock(test=IRInstr(Op.DebugLog, [IRConst(31)]))
        a = BasicBlock(statements=[IRSet(_sc("unused"), IRConst(1))])
        b = BasicBlock(statements=[IRSet(_sc("unused"), IRConst(2))])
        join = BasicBlock(statements=[_log(42)])
        entry.connect_to(a, 0)
        entry.connect_to(b, None)
        a.connect_to(join, None)
        b.connect_to(join, None)
        return entry

    assert not _tests(_optimized(build))
    assert "DebugLog(31)" in cfg_to_text(_optimized(build))
    assert _assert_semantics(build).log == [31, 42]


def _counted_loop(initial=0, bound=8, step=1, comparison=Op.Less, effect=False, nested=False):
    entry = BasicBlock(statements=[IRSet(_sc("i"), IRConst(initial))])
    header = BasicBlock(test=IRPureInstr(comparison, [_rd("i"), IRConst(bound)]))
    body = BasicBlock(statements=[_log(_rd("i"))] if effect else [])
    latch = BasicBlock(statements=[IRSet(_sc("i"), IRPureInstr(Op.Add, [_rd("i"), IRConst(step)]))])
    done = BasicBlock(statements=[_log(42)])
    entry.connect_to(header, None)
    header.connect_to(done, 0)
    header.connect_to(body, None)
    if nested:
        inner = BasicBlock(test=_w(0))
        body.connect_to(inner, None)
        inner.connect_to(inner, 0)
        inner.connect_to(latch, None)
    else:
        body.connect_to(latch, None)
    latch.connect_to(header, None)
    return entry


@pytest.mark.parametrize(
    ("initial", "bound", "step", "comparison"),
    [
        (0, 8, 1, Op.Less),
        (0, 8, 1, Op.LessOr),
        (8, 0, -1, Op.Greater),
        (8, 0, -1, Op.GreaterOr),
        (5, 0, 1, Op.Less),
        (16777214, 16777215, 1, Op.LessOr),
        (-16777214, -16777215, -1, Op.GreaterOr),
    ],
)
@pytest.mark.parametrize("cleanup", [False, True])
def test_dead_finite_counted_loop_disappears(initial, bound, step, comparison, cleanup):
    def build():
        return _counted_loop(initial, bound, step, comparison)

    assert not _tests(_optimized(build, cleanup=cleanup))
    assert _assert_semantics(build).log == [42]


@pytest.mark.parametrize(
    ("initial", "bound", "step", "comparison"),
    [
        (0, 8, -1, Op.Less),
        (0, 8, 0, Op.Less),
        (16777216, 16777218, 1, Op.Less),
        (-16777216, -16777218, -1, Op.Greater),
        (0, float("inf"), 1, Op.Less),
        (0.5, 8, 1, Op.Less),
    ],
)
def test_unproven_counter_loop_keeps_control(initial, bound, step, comparison):
    # These loops may not terminate in f32, so check their control flow without running them.
    def build():
        return _counted_loop(initial, bound, step, comparison)

    assert _tests(_optimized(build))


def test_finite_loop_with_live_effect_keeps_iterations():
    def build():
        return _counted_loop(effect=True)

    assert _tests(_optimized(build))
    assert _assert_semantics(build).log == [*range(8), 42]


def test_unknown_inner_cycle_prevents_outer_loop_deletion():
    def build():
        return _counted_loop(nested=True)

    assert _tests(_optimized(build))


def test_branch_into_closed_cycle_is_observable():
    def build():
        entry = BasicBlock(test=_w(0))
        cycle = BasicBlock()
        done = BasicBlock(statements=[_log(42)])
        entry.connect_to(cycle, 0)
        entry.connect_to(done, None)
        cycle.connect_to(cycle, None)
        return entry

    result = _optimized(build)
    assert _tests(result)
    assert any(edge.dst is block for block in traverse_cfg_reverse_postorder(result) for edge in block.outgoing)


def test_irreducible_cycle_with_exit_keeps_control():
    def build():
        entry = BasicBlock(test=_w(0))
        a = BasicBlock(test=_w(1))
        b = BasicBlock(test=_w(2))
        done = BasicBlock(statements=[_log(42)])
        entry.connect_to(a, 0)
        entry.connect_to(b, None)
        a.connect_to(b, 0)
        a.connect_to(done, None)
        b.connect_to(a, 0)
        b.connect_to(done, None)
        return entry

    assert len(_tests(_optimized(build))) == 3


def test_shifted_comparison_opposing_counter_step_is_not_proven_finite():
    # In f32, i stops increasing at 2**24 while i - 1 <= 2**24 - 1 remains true.
    def build():
        entry = _counted_loop(initial=16777215, bound=16777215, comparison=Op.LessOr)
        header = next(iter(entry.outgoing)).dst
        header.test = IRPureInstr(Op.LessOr, [IRPureInstr(Op.Subtract, [_rd("i"), IRConst(1)]), IRConst(16777215)])
        return entry

    assert _tests(_optimized(build))


def test_dead_branch_removed_in_full_cleanup_pipeline():
    def build():
        entry = BasicBlock(test=_w(0))
        a = BasicBlock(statements=[IRSet(_sc("unused"), _w(1))])
        b = BasicBlock(statements=[IRSet(_sc("unused"), _w(2))])
        join = BasicBlock(statements=[_log(41), _log(42)])
        entry.connect_to(a, 0)
        entry.connect_to(b, None)
        a.connect_to(join, None)
        b.connect_to(join, None)
        return entry

    assert not _tests(_optimized(build, cleanup=True))
    assert _assert_semantics(build).log == [41, 42]
