"""Boolean normalization with observable values, effects, and branch polarity."""

import math
import operator

import pytest

from sonolus.backend._opt import ir, midend  # ruff: ignore[import-private-name]
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import FAST_PASSES, MINIMAL_PASSES, STANDARD_PASSES, optimize_and_finalize
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_reverse_postorder
from sonolus.backend.place import BlockPlace, TempBlock
from sonolus.script.internal.context import ReadOnlyMemory

INPUT = 20
VALUES = [0.0, -0.0, 1.0, -2.0, 3.5, float("inf"), -float("inf"), float("nan")]
LEVELS = [MINIMAL_PASSES, FAST_PASSES, STANDARD_PASSES]


def _read(index=0):
    return IRGet(BlockPlace(INPUT, index))


def _log(value):
    return IRInstr(Op.DebugLog, [value])


def _not(value):
    return IRPureInstr(Op.Not, [value])


def _select(condition, positive=True):
    return IRPureInstr(Op.If, [condition, IRConst(int(positive)), IRConst(int(not positive))])


def _run(node, values):
    interpreter = Interpreter()
    interpreter.blocks[3000] = list(ReadOnlyMemory().values)
    interpreter.blocks[INPUT] = list(values)
    interpreter.run(node)
    return interpreter


def _count(node, op):
    result = 0
    pending = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, FunctionNode):
            result += current.func == op
            pending.extend(current.args)
    return result


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("positive", [False, True])
def test_literal_boolean_select_preserves_numeric_truthiness(level, positive):
    node = optimize_and_finalize(BasicBlock(statements=[_log(_select(_read(), positive))]), level)
    for value in VALUES:
        expected = float(value != 0) if positive else float(value == 0)
        assert _run(node, [value]).log == [expected]
    if level is not MINIMAL_PASSES:
        assert _count(node, Op.If) == 0
        assert _count(node, Op.NotEqual if positive else Op.Not) == 1


@pytest.mark.parametrize("level", [FAST_PASSES, STANDARD_PASSES])
@pytest.mark.parametrize("kind", ["comparison", "not", "and", "or", "select"])
def test_select_forwards_a_proven_boolean_condition(level, kind):
    comparison = IRPureInstr(Op.Less, [_read(), _read(1)])
    condition = comparison
    if kind == "not":
        condition = _not(_read())
    elif kind == "and":
        condition = IRPureInstr(Op.And, [comparison, _not(_read())])
    elif kind == "or":
        condition = IRPureInstr(Op.Or, [comparison, _not(_read())])
    elif kind == "select":
        condition = IRPureInstr(Op.If, [_read(2), comparison, _not(_read())])
    node = optimize_and_finalize(BasicBlock(statements=[_log(_select(condition))]), level)
    assert _count(node, Op.If) == int(kind == "select")
    for left in VALUES:
        for selector in [0, 1]:
            expected = left < 2
            if kind == "not":
                expected = left == 0
            elif kind == "and":
                expected = expected and left == 0
            elif kind == "or":
                expected = expected or left == 0
            elif kind == "select" and not selector:
                expected = left == 0
            assert _run(node, [left, 2, selector]).log == [float(expected)]


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("positive", [False, True])
def test_select_evaluates_an_effectful_condition_once(level, positive):
    condition = IRInstr(Op.IncrementPost, [IRConst(INPUT), IRConst(0)])
    node = optimize_and_finalize(BasicBlock(statements=[_log(_select(condition, positive)), _log(_read())]), level)
    for value in [-1, 0, 4]:
        expected = float(value + 1 != 0) if positive else float(value + 1 == 0)
        assert _run(node, [value]).log == [expected, value + 1]


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("positive", [False, True])
def test_select_preserves_random_call_order(level, positive, monkeypatch):
    def random_value():
        return IRInstr(Op.Random, [IRConst(0), IRConst(1)])

    node = optimize_and_finalize(
        BasicBlock(statements=[_log(_select(random_value(), positive)), _log(random_value())]), level
    )
    values = iter([0.0, 0.75])
    monkeypatch.setattr("sonolus.backend.interpret.random.uniform", lambda *_: next(values))
    assert _run(node, []).log == [float(not positive), 0.75]
    assert list(values) == []


@pytest.mark.parametrize("level", [FAST_PASSES, STANDARD_PASSES])
@pytest.mark.parametrize("op", [Op.Equal, Op.NotEqual])
def test_negative_select_of_comparison_reuses_complement(level, op):
    node = optimize_and_finalize(
        BasicBlock(statements=[_log(_select(IRPureInstr(op, [_read(), _read(1)]), False))]), level
    )
    assert _count(node, Op.If) == 0
    assert _count(node, Op.Not) == 0
    assert _count(node, Op.NotEqual if op == Op.Equal else Op.Equal) == 1
    assert _run(node, [float("nan"), float("nan")]).log == [float(op == Op.Equal)]


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize(("op", "compare"), [(Op.Equal, operator.eq), (Op.NotEqual, operator.ne)])
def test_inverted_equality_preserves_nan_and_shared_results(level, op, compare):
    result = BlockPlace(TempBlock("comparison", 1), 0)
    node = optimize_and_finalize(
        BasicBlock(
            statements=[
                IRSet(result, IRPureInstr(op, [_read(), _read(1)])),
                _log(IRGet(result)),
                _log(_not(IRGet(result))),
            ]
        ),
        level,
    )
    for left in VALUES:
        for right in [0, 3.5, float("nan")]:
            expected = compare(left, right)
            assert _run(node, [left, right]).log == [float(expected), float(not expected)]
    if level is not MINIMAL_PASSES:
        assert _count(node, Op.Set) <= 1
        assert _count(node, Op.Get) <= 3


@pytest.mark.parametrize("level", [FAST_PASSES, STANDARD_PASSES])
@pytest.mark.parametrize(("op", "compare"), [(Op.Equal, operator.eq), (Op.NotEqual, operator.ne)])
def test_inverted_comparison_keeps_sharing_created_by_later_cse(level, op, compare):
    left_value = BlockPlace(TempBlock("left_value", 1), 0)
    right_value = BlockPlace(TempBlock("right_value", 1), 0)
    node = optimize_and_finalize(
        BasicBlock(
            statements=[
                IRSet(left_value, _read()),
                IRSet(right_value, _read(1)),
                _log(_not(IRPureInstr(op, [IRGet(left_value), IRGet(right_value)]))),
                _log(IRPureInstr(op, [IRGet(left_value), IRGet(right_value)])),
            ]
        ),
        level,
    )
    assert _count(node, Op.Set) <= 1
    assert _count(node, Op.Get) <= 3
    for left in VALUES:
        for right in [0, 3.5, float("nan")]:
            expected = compare(left, right)
            assert _run(node, [left, right]).log == [float(not expected), float(expected)]


@pytest.mark.parametrize("level", [FAST_PASSES, STANDARD_PASSES])
@pytest.mark.parametrize(("op", "compare"), [(Op.Equal, operator.eq), (Op.NotEqual, operator.ne)])
@pytest.mark.parametrize("boundary", ["effect", "block"])
def test_delayed_comparison_complement_keeps_one_materialized_value(level, op, compare, boundary):
    def build(inverted):
        predicate = BlockPlace(TempBlock("delayed_predicate", 1), 0)
        result = _not(IRGet(predicate)) if inverted else IRGet(predicate)
        entry = BasicBlock(statements=[IRSet(predicate, IRPureInstr(op, [_read(), _read(1)]))])
        if boundary == "effect":
            entry.statements.extend([_log(IRConst(7)), _log(result)])
            return entry
        marker = BlockPlace(TempBlock("marker", 1), 0)
        entry.test = _read(2)
        false = BasicBlock(statements=[IRSet(marker, IRConst(100))])
        true = BasicBlock(statements=[IRSet(marker, IRConst(200))])
        join = BasicBlock(statements=[_log(result), _log(IRGet(marker)), _log(IRConst(9))])
        entry.connect_to(false, 0)
        entry.connect_to(true, None)
        false.connect_to(join, None)
        true.connect_to(join, None)
        return entry

    node = optimize_and_finalize(build(True), level)
    direct = optimize_and_finalize(build(False), level)
    assert _count(node, Op.Set) <= _count(direct, Op.Set)
    assert _count(node, Op.Get) <= _count(direct, Op.Get)
    for left in VALUES:
        for right in [0, 3.5, float("nan")]:
            for selector in [0, 1]:
                expected = float(not compare(left, right))
                log = [7, expected] if boundary == "effect" else [expected, 200 if selector else 100, 9]
                assert _run(node, [left, right, selector]).log == log


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("op", [Op.Equal, Op.NotEqual])
def test_inverted_equality_keeps_operand_effect_order(level, op):
    first = IRInstr(Op.IncrementPost, [IRConst(INPUT), IRConst(0)])
    node = optimize_and_finalize(
        BasicBlock(statements=[_log(_not(IRPureInstr(op, [first, _read()]))), _log(_read())]), level
    )
    assert _run(node, [4]).log == [float(op == Op.NotEqual), 5]


@pytest.mark.parametrize("op", [Op.Less, Op.LessOr, Op.Greater, Op.GreaterOr])
def test_inverted_ordered_comparison_retains_not_for_nan(op):
    node = optimize_and_finalize(
        BasicBlock(statements=[_log(_not(IRPureInstr(op, [_read(), _read(1)])))]), STANDARD_PASSES
    )
    assert _count(node, Op.Not) == 1
    assert _run(node, [float("nan"), 0]).log == [1]


def _branch(test, cases=(0, None)):
    entry = BasicBlock(test=test)
    value = BlockPlace(TempBlock("branch_value", 1), 0)
    join = BasicBlock(statements=[_log(IRGet(value))])
    for index, case in enumerate(cases):
        arm = BasicBlock(statements=[IRSet(value, IRConst(10 + index)), _log(IRConst(index))])
        entry.connect_to(arm, case)
        arm.connect_to(join, None)
    return entry


@pytest.mark.parametrize("level", [FAST_PASSES, STANDARD_PASSES])
@pytest.mark.parametrize("op", [Op.Equal, Op.NotEqual])
@pytest.mark.parametrize("zero_first", [False, True])
@pytest.mark.parametrize("inverted", [False, True])
def test_zero_comparison_branch_preserves_edge_polarity(level, op, zero_first, inverted):
    args = [IRConst(0), _read()] if zero_first else [_read(), IRConst(0)]
    test = IRPureInstr(op, args)
    if inverted:
        test = _not(test)
    node = optimize_and_finalize(_branch(test), level)
    assert _count(node, Op.Equal) == 0
    assert _count(node, Op.NotEqual) == 0
    assert _count(node, Op.Not) == 0
    for value in VALUES:
        selected = value == 0 if op == Op.Equal else value != 0
        if inverted:
            selected = not selected
        index = int(selected)
        assert _run(node, [value]).log == [index, 10 + index]


@pytest.mark.parametrize("level", [FAST_PASSES, STANDARD_PASSES])
@pytest.mark.parametrize("op", [Op.Equal, Op.NotEqual])
@pytest.mark.parametrize("effectful", [False, True])
def test_shared_zero_comparison_does_not_materialize_its_operand(level, op, effectful):
    def build(bound):
        condition = IRInstr(Op.IncrementPost, [IRConst(INPUT), IRConst(0)]) if effectful else _read()
        predicate = BlockPlace(TempBlock("shared_predicate", 1), 0)
        entry = BasicBlock(
            statements=[IRSet(predicate, IRPureInstr(op, [condition, IRConst(bound)]))], test=IRGet(predicate)
        )
        false = BasicBlock(statements=[_log(IRConst(100))])
        true = BasicBlock(statements=[IRSet(predicate, IRConst(7)), _log(IRConst(200))])
        join = BasicBlock(statements=[_log(IRGet(predicate))])
        entry.connect_to(false, 0)
        entry.connect_to(true, None)
        false.connect_to(join, None)
        true.connect_to(join, None)
        return entry

    node = optimize_and_finalize(build(0), level)
    direct = optimize_and_finalize(build(2), level)
    assert _count(node, Op.Set) <= _count(direct, Op.Set)
    assert _count(node, Op.Get) <= _count(direct, Op.Get)
    for value in VALUES:
        tested = value + 1 if effectful else value
        selected = tested == 0 if op == Op.Equal else tested != 0
        assert _run(node, [value]).log == ([200, 7] if selected else [100, 0])


def _branch_only(cfg, monkeypatch):
    def canonicalize(func):
        midend._canon_branch_not(func)
        return func

    monkeypatch.setitem(ir._PHASE_REGISTRY, "boolean_branch_test", canonicalize)
    return ir.debug_run(cfg, phases=["ssa", "boolean_branch_test"])


@pytest.mark.parametrize("op", [Op.Equal, Op.NotEqual])
def test_shared_not_keeps_its_comparison_when_branch_is_peeled(op, monkeypatch):
    predicate = BlockPlace(TempBlock("shared_not", 1), 0)
    entry = _branch(IRGet(predicate))
    entry.statements = [
        IRSet(predicate, _not(IRPureInstr(op, [_read(), IRConst(0)]))),
        _log(IRGet(predicate)),
    ]
    cfg = _branch_only(entry, monkeypatch)
    comparison = next(stmt.value for stmt in cfg.statements if isinstance(stmt, IRSet) and stmt.place == cfg.test.place)
    assert isinstance(comparison, IRPureInstr)
    assert comparison.op == op
    assert any(
        isinstance(stmt, IRSet) and isinstance(stmt.value, IRPureInstr) and stmt.value.op == Op.Not
        for stmt in cfg.statements
    )


@pytest.mark.parametrize("address_kind", ["index", "block"])
def test_place_address_counts_as_a_shared_comparison_use(address_kind, monkeypatch):
    predicate = BlockPlace(TempBlock("address_predicate", 1), 0)
    address = BlockPlace(INPUT, IRGet(predicate)) if address_kind == "index" else BlockPlace(IRGet(predicate), 0)
    entry = _branch(IRGet(predicate))
    entry.statements = [
        IRSet(predicate, IRPureInstr(Op.NotEqual, [_read(), IRConst(0)])),
        _log(IRGet(address)),
    ]
    cfg = _branch_only(entry, monkeypatch)
    comparison = next(stmt.value for stmt in cfg.statements if isinstance(stmt, IRSet) and stmt.place == cfg.test.place)
    assert isinstance(comparison, IRPureInstr)
    assert comparison.op == Op.NotEqual


@pytest.mark.parametrize("op", [Op.Equal, Op.NotEqual])
@pytest.mark.parametrize("cases", [(1, None), (0, 1, None)])
def test_zero_comparison_keeps_noncanonical_branch_cases(op, cases):
    cfg = ir.debug_run(_branch(IRPureInstr(op, [_read(), IRConst(0)]), cases), phases=["ssa", "midend"])
    assert isinstance(cfg.test, IRGet)
    comparison = next(stmt.value for stmt in cfg.statements if isinstance(stmt, IRSet) and stmt.place == cfg.test.place)
    assert isinstance(comparison, IRPureInstr)
    assert comparison.op == op
    assert any(block.phis for block in traverse_cfg_reverse_postorder(cfg))


@pytest.mark.parametrize("level", LEVELS)
def test_undef_boolean_merge_is_normalized_instead_of_forwarded(level):
    value = BlockPlace(TempBlock("maybe_boolean", 1), 0)
    entry = BasicBlock(test=_read())
    defined = BasicBlock(statements=[IRSet(value, IRConst(0))])
    join = BasicBlock(statements=[_log(_select(IRGet(value)))])
    entry.connect_to(join, 0)
    entry.connect_to(defined, None)
    defined.connect_to(join, None)
    node = optimize_and_finalize(entry, level)
    assert _run(node, [0]).log == [1]
    assert _run(node, [1]).log == [0]


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("negative_arm", [False, True])
def test_select_keeps_negative_zero_result_arms(level, negative_arm):
    negative_zero = IRPureInstr(Op.Negate, [IRConst(0)])
    arms = [negative_zero, IRConst(1)] if negative_arm else [IRConst(1), negative_zero]
    node = optimize_and_finalize(BasicBlock(statements=[_log(IRPureInstr(Op.If, [_read(), *arms]))]), level)
    result = _run(node, [int(negative_arm)]).log[0]
    assert result == 0
    assert math.copysign(1, result) == -1


@pytest.mark.parametrize("level", LEVELS)
def test_boolean_select_over_negative_zero_phi_returns_positive_zero(level):
    value = BlockPlace(TempBlock("signed_boolean", 1), 0)
    entry = BasicBlock(test=_read())
    zero = BasicBlock(statements=[IRSet(value, IRPureInstr(Op.Negate, [IRConst(0)]))])
    one = BasicBlock(statements=[IRSet(value, IRConst(1))])
    join = BasicBlock(statements=[_log(_select(IRGet(value)))])
    entry.connect_to(zero, 0)
    entry.connect_to(one, None)
    zero.connect_to(join, None)
    one.connect_to(join, None)
    node = optimize_and_finalize(entry, level)
    result = _run(node, [0]).log[0]
    assert result == 0
    assert math.copysign(1, result) == 1
    assert _run(node, [1]).log == [1]
