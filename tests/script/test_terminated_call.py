"""Tests for calls whose every traced path terminates.

PYTEST_DONT_REWRITE

A callee that terminates on every traced path (a compile-time-false assert_true, debug.error, or the
documented `assert False` unreachable pattern) has no value to return, so it leaves the context dead and
hands back a placeholder instead. Every position that would read such a value checks `ctx().live` first and
resumes the way its own statically-false path does. The temp-variable spelling `v = helper(-1); return v.x`
already compiles to a terminate; these tests pin that inline spellings of the same program compile too, in
every position a value can be read or a construct can be built from one.

Compiling is the weaker half of that claim, so each shape is pinned to a real terminate where it can be:
`run_and_validate` asserts one whenever its plain-Python leg raises, and `run_gated` asserts that the
terminating arm of a runtime branch stops while the other arm still falls through. Both gate values matter:
a shape that compiles by statically skipping the terminate reads as green against the compiling half alone.
The rest use `run_compiled`, each for a reason given at its own site or because compiling is genuinely all
it claims: that a position which compiled before still compiles, that no statement after a terminating call
is visited, or that a callee terminating on only one path still returns a value. Those cannot run as plain
Python either, since they read `Mem.gate` to keep a branch runtime-valued (a level-memory read raises
outside compilation) or name a variable that is deliberately never defined.
"""

import pytest

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.optimize import STANDARD_PASSES, OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.backend.place import BlockPlace
from sonolus.script.array import Array
from sonolus.script.debug import assert_true, error
from sonolus.script.globals import level_memory
from sonolus.script.internal.context import RuntimeChecks, ctx
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.visitor import compile_and_call
from sonolus.script.iterator import SonolusIterator
from sonolus.script.maybe import Maybe
from sonolus.script.num import Num
from sonolus.script.record import Record
from sonolus.script.sprite import Sprite, SpriteGroup, skin, sprite, sprite_group
from sonolus.script.vec import Vec2
from tests.script.conftest import compile_fn, run_and_validate, run_compiled


@level_memory
class Mem:
    gate: float
    vec: Vec2


@skin
class Sk:
    solo: Sprite = sprite("Solo")
    grp: SpriteGroup = sprite_group(["a", "b", "c"])


def bad_vec(i) -> Vec2:
    assert_true(i >= 0, "index must be non-negative")
    return Vec2(i, i)


@meta_fn
def fail_if_applied_as_decorator(decorated):
    raise RuntimeError("outer decorator must not be applied")


def unreachable_vec(i) -> Vec2:
    if i == 0:
        return Vec2(1, 1)
    assert False, "unreachable"  # noqa: B011, PT015


def bad_pair(i):
    assert_true(i >= 0, "index must be non-negative")
    return Vec2(i, i), i


def erroring_vec() -> Vec2:
    error("boom")
    return Vec2(1, 1)


def one_path_terminates(g) -> Vec2:
    if g > 0:
        error("boom")
    return Vec2(2, 2)


def bad_arr(i) -> Array[float, 2]:
    assert_true(i >= 0, "index must be non-negative")
    return Array(1.0, 2.0)


def bad_mapping(i):
    assert_true(i >= 0, "index must be non-negative")
    return {"a": 1.0}


class BadIterMethod(Record):
    """An iterable whose `__iter__` terminates on every traced path."""

    v: float

    def __iter__(self):
        error("no iterator")


class BadNextIterator(Record, SonolusIterator):
    """An iterator whose `next` terminates on every traced path."""

    v: float

    def next(self) -> Maybe[float]:
        error("no next")


class BadIaddValue(Record):
    """A value whose `__iadd__` terminates on every traced path."""

    v: float

    def __iadd__(self, other):
        error("no iadd")


class BadEqValue(Record):
    """A value whose `__eq__` terminates on every traced path."""

    v: float

    def __eq__(self, other):
        error("no eq")

    def __hash__(self):
        raise TypeError("unhashable type: 'BadEqValue'")


class BadPropValue(Record):
    """A value whose property `p` terminates on every traced path."""

    v: float

    @property
    def p(self) -> float:
        error("no prop")


def yields_bad_x():
    yield bad_vec(-1).x


def yields_from_bad_arr():
    yield from bad_arr(-1)


def yields_from_yields_bad_x():
    yield from yields_bad_x()


def yields_then_returns_bad_x():
    yield 1.0
    # A terminating value in a generator's return is what this helper exists to exercise.
    return bad_vec(-1).x  # noqa: B901


GATE, MARK, RES = -3, -1, -2


def run_gated(body, gate_value, runtime_checks=RuntimeChecks.NONE):
    """Compile `body(gate)` with the gate read from a scratch block and interpret it with the gate seeded.

    Returns (mark, result): mark is 1 only if control fell out of the call into the following statement,
    so a terminating gate arm leaves it at 0. This pins that a compiling spelling ships a real terminate
    rather than falling through.
    """

    @meta_fn
    def wrapper():
        gate = Num._from_place_(BlockPlace(GATE, 0))
        result = compile_and_call(body, gate)
        if not ctx().live:
            # A body that terminates at every gate has no result to record. The stores below emit nothing in
            # a dead context anyway, so mark stays 0 either way; skipping them keeps the placeholder out of
            # Num._set_, which would reject it on the host.
            return 0
        # Only reached if control actually falls out of the call.
        Num._from_place_(BlockPlace(MARK, 0))._set_(Num(1))
        Num._from_place_(BlockPlace(RES, 0))._set_(result)
        return 0

    cfg, rom = compile_fn(wrapper, runtime_checks=runtime_checks)
    entry = cfg_to_engine_node(run_passes(cfg, STANDARD_PASSES, OptimizerConfig()))
    interpreter = Interpreter()
    interpreter.blocks[PlayBlock.EngineRom] = list(rom)
    interpreter.blocks[GATE] = [gate_value]
    interpreter.blocks[MARK] = [0.0]
    interpreter.blocks[RES] = [0.0]
    interpreter.run(entry)
    return interpreter.get(MARK, 0), interpreter.get(RES, 0)


def test_inline_attribute_read_compiles_and_ships_a_terminate():
    def inline_form(gate):
        if gate > 0:
            return bad_vec(-1).x
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_control_non_terminating_callee_falls_through():
    def control_form(gate):
        if gate > 0:
            return bad_vec(1).x
        return 5.0

    assert run_gated(control_form, 1.0) == (1.0, 1.0)


def test_inline_attribute_read_compiles():
    def fn():
        return bad_vec(-1).x

    with pytest.raises(AssertionError, match="index must be non-negative"):
        run_and_validate(fn)


def test_inline_attribute_read_compiles_with_notify_and_terminate():
    # run_compiled: run_and_validate never reaches NOTIFY_AND_TERMINATE, the level that emits a debug log
    # and a pause ahead of the terminate.
    def fn():
        return bad_vec(-1).x

    assert run_compiled(fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE) == 0


def test_inline_attribute_read_after_error_compiles():
    def fn():
        return erroring_vec().x

    with pytest.raises(RuntimeError, match="boom"):
        run_and_validate(fn)


def test_documented_assert_false_pattern_compiles_inline():
    # debug.assert_true documents that a compile-time-false condition terminates rather than erroring so
    # the `assert False` unreachable pattern compiles; the inline spelling must keep that promise too.
    def fn():
        return unreachable_vec(1).x

    with pytest.raises(AssertionError, match="unreachable"):
        run_and_validate(fn)


def test_operator_on_terminating_call_compiles():
    def fn(gate):
        if gate > 0:
            return (bad_vec(-1) + Vec2(1, 1)).x
        return 5.0

    with pytest.raises(AssertionError, match="index must be non-negative"):
        run_and_validate(fn, 1.0)
    assert run_gated(fn, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(fn, 1.0)
    assert mark == 0.0


def test_store_of_terminating_call_compiles():
    def fn(gate):
        if gate > 0:
            a = Array(Vec2(0, 0))
            a[0] = bad_vec(-1)
            return 0.0
        return 5.0

    with pytest.raises(AssertionError, match="index must be non-negative"):
        run_and_validate(fn, 1.0)
    assert run_gated(fn, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(fn, 1.0)
    assert mark == 0.0


def test_copy_assign_of_terminating_call_compiles():
    def fn(gate):
        if gate > 0:
            Mem.vec @= bad_vec(-1)
            return 1.0
        return 5.0

    assert run_gated(fn, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(fn, 1.0)
    assert mark == 0.0


def test_unpack_of_terminating_call_compiles():
    def fn(gate):
        if gate > 0:
            x, y = bad_pair(-1)
            return x.x + y
        return 5.0

    with pytest.raises(AssertionError, match="index must be non-negative"):
        run_and_validate(fn, 1.0)
    assert run_gated(fn, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(fn, 1.0)
    assert mark == 0.0


def test_boolop_over_terminating_call_compiles():
    # The left operand short-circuits at gate 1, so 2 is the gate that reaches the terminating right operand.
    def fn(gate):
        if gate > 0:
            b = (gate > 1) and bad_vec(-1)  # noqa: F841
            return 1.0
        return 5.0

    with pytest.raises(AssertionError, match="index must be non-negative"):
        run_and_validate(fn, 2.0)
    assert run_gated(fn, 0.0) == (1.0, 5.0)
    assert run_gated(fn, 1.0) == (1.0, 1.0)
    mark, _ = run_gated(fn, 2.0)
    assert mark == 0.0


def test_comparison_of_terminating_call_compiles():
    def fn(gate):
        if gate > 0:
            b = bad_vec(-1) == Vec2(1, 1)  # noqa: F841
            return 1.0
        return 5.0

    with pytest.raises(AssertionError, match="index must be non-negative"):
        run_and_validate(fn, 1.0)
    assert run_gated(fn, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(fn, 1.0)
    assert mark == 0.0


def test_genexpr_over_terminating_call_compiles():
    def fn(gate):
        if gate > 0:
            return sum(v.x for v in bad_vec(-1))
        return 5.0

    with pytest.raises(AssertionError, match="index must be non-negative"):
        run_and_validate(fn, 1.0)
    assert run_gated(fn, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(fn, 1.0)
    assert mark == 0.0


def test_genexpr_elt_of_terminating_call_ships_a_terminate():
    # The elt position, unlike the iterable position above: here every yield path of the genexpr dies
    # inside the state machine body, so the terminate lives there and the consumer must still enter it.
    def inline_form(gate):
        if gate > 0:
            return max(bad_vec(-1).x for _ in Array(1.0, 2.0))
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_lambda_body_of_terminating_call_ships_a_terminate():
    # A lambda's body is a bare expression, so its trace has no statement loop to stop at. The whole-callback
    # form is the one that leaves the caller reading `$return` off the lambda's own dead scope; the gated
    # form drops that context at the merge.
    def whole_callback():
        f = lambda: bad_vec(-1).x  # noqa: E731

        return f()

    def inline_form(gate):
        f = lambda: bad_vec(-1).x  # noqa: E731

        if gate > 0:
            return f()
        return 5.0

    with pytest.raises(AssertionError, match="index must be non-negative"):
        run_and_validate(whole_callback)
    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_generator_yield_of_terminating_call_ships_a_terminate():
    def for_form(gate):
        if gate > 0:
            total = 0.0
            for v in yields_bad_x():
                total += v
            return total
        return 5.0

    def sum_form(gate):
        # sum() dispatches on the element type, so it sees the yielded value where the loop above only
        # binds it.
        if gate > 0:
            return sum(yields_bad_x())
        return 5.0

    for form in (for_form, sum_form):
        assert run_gated(form, 0.0) == (1.0, 5.0)
        mark, _ = run_gated(form, 1.0)
        assert mark == 0.0


def test_generator_return_of_terminating_call_ships_a_terminate():
    def inline_form(gate):
        if gate > 0:
            total = 0.0
            for v in yields_then_returns_bad_x():
                total += v
            return total
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_genexpr_elt_terminates_only_on_the_iterations_that_run():
    # The trip count is runtime-valued, so an empty genexpr must still fall through with the empty-case
    # result: the elt fix must not trade the terminate for statically skipping the whole state machine.
    def inline_form(gate):
        total = 7.0
        for v in (bad_vec(-1).x for _ in range(gate)):
            total += v
        return total

    assert run_gated(inline_form, 0.0) == (1.0, 7.0)
    mark, _ = run_gated(inline_form, 3.0)
    assert mark == 0.0


def test_sprite_group_constant_out_of_range_draw_compiles():
    # The shape real API code reaches: a constant sprite_group index outside the group, whose bounds check
    # is the terminating call.
    def fn(gate):
        if gate > 0:
            Sk.grp[-1].draw(Vec2(0, 0), Vec2(0, 1), Vec2(1, 1), Vec2(1, 0), 0)
            return 1.0
        return 5.0

    assert run_gated(fn, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(fn, 1.0)
    assert mark == 0.0


def test_sprite_group_past_the_end_attribute_compiles():
    def fn(gate):
        if gate > 0:
            return Sk.grp[3].id
        return 5.0

    assert run_gated(fn, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(fn, 1.0)
    assert mark == 0.0


def test_previously_compiling_dead_shapes_still_compile():
    # Positions that accept a compile-time constant, which is what the terminating call's result would have
    # had to be: a dict key, a set element, the right operand of `is`. The placeholder is one, and the
    # statement it lands in is dead, so they keep compiling.
    def dict_key():
        if Mem.gate > 0:
            d = {bad_vec(-1): 1}  # noqa: F841
        return 5.0

    def set_element():
        if Mem.gate > 0:
            s = {bad_vec(-1)}  # noqa: F841
        return 5.0

    def is_comparison():
        if Mem.gate > 0:
            b = Mem.gate is bad_vec(-1)  # noqa: F841
        return 5.0

    assert run_compiled(dict_key, runtime_checks=RuntimeChecks.NONE) == 5
    assert run_compiled(set_element, runtime_checks=RuntimeChecks.NONE) == 5
    assert run_compiled(is_comparison, runtime_checks=RuntimeChecks.NONE) == 5


def test_statement_after_terminating_call_is_not_visited():
    # The deliberate truncation: after a terminating call, the next statement is never visited, so even an
    # undefined name there is accepted. This is pinned so a later change cannot silently narrow it.
    def fn():
        v = bad_vec(-1)
        return no_such_name_anywhere + v  # noqa: F821

    assert run_compiled(fn, runtime_checks=RuntimeChecks.NONE) == 0


def test_rest_of_the_statement_after_a_terminating_call_is_still_traced():
    # The truncation is per statement, not per expression: what follows the terminating call inside the same
    # statement is still traced, in the dead context, where it emits nothing but does still report an
    # undefined name or an unsupported construct.
    def fn():
        return bad_vec(-1).x + no_such_name_anywhere  # noqa: F821

    with pytest.raises(CompilationError, match="Name no_such_name_anywhere is not defined"):
        run_compiled(fn, runtime_checks=RuntimeChecks.NONE)


def test_statement_after_live_call_is_visited():
    def fn():
        v = bad_vec(1)
        return no_such_name_anywhere + v  # noqa: F821

    with pytest.raises(CompilationError, match="Name no_such_name_anywhere is not defined"):
        run_compiled(fn, runtime_checks=RuntimeChecks.NONE)


def test_partially_terminating_callee_still_returns():
    def fn():
        return one_path_terminates(Mem.gate).x

    assert run_compiled(fn, runtime_checks=RuntimeChecks.NONE) == 2


def test_genexpr_later_clause_over_terminating_call_ships_a_terminate():
    # A comprehension clause after the first, in both of construct_genexpr's arms: a runtime outer
    # clause takes the iterator arm, a literal-tuple outer clause the unrolled one.
    def runtime_outer(gate):
        if gate > 0:
            return sum(a + b for a in Array(1.0, 2.0) for b in bad_arr(-1))
        return 5.0

    def tuple_outer(gate):
        if gate > 0:
            return sum(a + b for a in (1.0, 2.0) for b in bad_arr(-1))
        return 5.0

    def third_clause(gate):
        if gate > 0:
            return sum(a + b + c for a in Array(1.0, 2.0) for b in Array(3.0, 4.0) for c in bad_arr(-1))
        return 5.0

    for form in (runtime_outer, tuple_outer, third_clause):
        assert run_gated(form, 0.0) == (1.0, 5.0)
        mark, _ = run_gated(form, 1.0)
        assert mark == 0.0


def test_yield_from_terminating_call_ships_a_terminate():
    def inline_form(gate):
        if gate > 0:
            total = 0.0
            for v in yields_from_bad_arr():
                total += v
            return total
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_yield_from_generator_whose_yields_all_terminate_ships_a_terminate():
    # The delegated generator yields nothing that can be observed, so its `next` is empty at compile time
    # the way a statically empty iterator's is. The terminate still has to ship: a delegation compiled away
    # as statically empty would leave mark at 1 with a 0 result.
    def inline_form(gate):
        if gate > 0:
            total = 0.0
            for v in yields_from_yields_bad_x():
                total += v
            return total
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_terminating_iter_dunder_ships_a_terminate():
    def inline_form(gate):
        if gate > 0:
            total = 0.0
            for v in BadIterMethod(1.0):
                total += v
            return total
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_terminating_next_ships_a_terminate():
    def inline_form(gate):
        if gate > 0:
            total = 0.0
            for v in BadNextIterator(1.0):
                total += v
            return total
        return 5.0

    def nested_form(gate):
        # The inner loop is abandoned after its header is pushed, so its frame must be popped: the
        # enclosing loop pops next, and popping the inner frame would close the wrong loop.
        if gate > 0:
            total = 0.0
            for i in Array(1.0, 2.0):
                for v in BadNextIterator(1.0):
                    total += v
                total += i
            return total
        return 5.0

    for form in (inline_form, nested_form):
        assert run_gated(form, 0.0) == (1.0, 5.0)
        mark, _ = run_gated(form, 1.0)
        assert mark == 0.0


def test_terminating_inplace_op_ships_a_terminate():
    def inline_form(gate):
        if gate > 0:
            v = BadIaddValue(1.0)
            v += 1.0
            return v.v
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_boolop_over_terminating_call_keeps_the_short_circuit_path():
    # The `and`'s left-false path is a live sibling of the branch the terminating right operand dies
    # in. Losing its edge would not fail loudly: a block with one outgoing edge is emitted as an
    # unconditional jump and its test is ignored, so the gate == 1 arm would terminate too.
    def inline_form(gate):
        if gate > 0:
            if (gate > 1) and bad_vec(-1).x > 0:
                return 1.0
            return 2.0
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    assert run_gated(inline_form, 1.0) == (1.0, 2.0)
    mark, _ = run_gated(inline_form, 2.0)
    assert mark == 0.0


def test_conditional_expression_over_terminating_call_keeps_the_other_arm():
    def inline_form(gate):
        if gate > 0:
            return bad_vec(-1).x if gate > 1 else 3.0
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    assert run_gated(inline_form, 1.0) == (1.0, 3.0)
    mark, _ = run_gated(inline_form, 2.0)
    assert mark == 0.0


def test_loop_target_of_terminating_call_terminates_only_on_the_iterations_that_run():
    # A loop target is an attribute whose base call terminates, so the assignment dies inside the loop body
    # while the loop's own exit stays live: a zero-trip run must still fall through to the statement after it.
    def inline_form(gate):
        total = 7.0
        for bad_vec(-1).x in range(gate):
            total += 1.0
        return total

    assert run_gated(inline_form, 0.0) == (1.0, 7.0)
    mark, _ = run_gated(inline_form, 3.0)
    assert mark == 0.0


def test_unrolled_loop_target_of_terminating_call_closes_its_frame():
    # The unrolled arm of the same shape, inside another loop: the inner loop's frame has to be closed, or
    # the enclosing one pops it and closes itself against the wrong loop.
    def inline_form(gate):
        total = 7.0
        for _ in range(gate):
            for bad_vec(-1).x in (1.0, 2.0):
                total += 1.0
        return total

    assert run_gated(inline_form, 0.0) == (1.0, 7.0)
    mark, _ = run_gated(inline_form, 3.0)
    assert mark == 0.0


def test_genexpr_target_of_terminating_call_ships_a_terminate():
    def inline_form(gate):
        total = 7.0
        for v in (1.0 for bad_vec(-1).x in range(gate)):
            total += v
        return total

    assert run_gated(inline_form, 0.0) == (1.0, 7.0)
    mark, _ = run_gated(inline_form, 3.0)
    assert mark == 0.0


def test_match_value_pattern_over_terminating_eq_ships_a_terminate():
    def inline_form(gate):
        if gate > 0:
            match BadEqValue(1.0):
                case 1.0:
                    return 1.0
            return 2.0
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_chained_comparison_over_terminating_call_keeps_the_false_path():
    def inline_form(gate):
        if gate > 0:
            if 1.0 < gate < bad_vec(-1).x:
                return 1.0
            return 2.0
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    assert run_gated(inline_form, 1.0) == (1.0, 2.0)
    mark, _ = run_gated(inline_form, 2.0)
    assert mark == 0.0


# The tests below pin one shape per remaining construct position that resumes after a terminating call. Ten
# of these positions have already opened a branch by the time the call terminates, and there a lost resume is
# not a loud failure: a block left with a single outgoing edge is emitted as an unconditional jump with its
# test ignored, so the live sibling arm would silently terminate too. The non-terminating gate is what
# catches that, which is why both gates are asserted everywhere.
#
# A shape discriminates the check at its own position only when the continuation it reads is the one that
# check supplies; where an enclosing one resumes identically, it stays green with that check deleted. These
# therefore pin the position's behavior rather than every check individually. The match-guard test below is
# shaped to read its own position's continuation for exactly that reason.


def test_genexpr_unrolled_filter_over_terminating_call_ships_a_terminate():
    # A filter over an unrolled (compile-time tuple) source: the element is skipped like a statically false
    # filter's, and the terminate still ships on the arm that runs the filter.
    def inline_form(gate):
        if gate > 0:
            return sum(a for a in (1.0, 2.0) if bad_vec(-1).x > 0)
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_genexpr_filter_over_terminating_call_ships_a_terminate():
    # The same filter position over a runtime iterable, whose element context is a loop body.
    def inline_form(gate):
        if gate > 0:
            return sum(a for a in Array(1.0, 2.0) if bad_vec(-1).x > 0)
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_genexpr_later_clause_terminating_iter_dunder_ships_a_terminate():
    # A later clause whose iterable constructs fine but whose `__iter__` terminates: distinct from the
    # later-clause test above, where the iterable expression itself is the terminating call.
    def inline_form(gate):
        if gate > 0:
            return sum(a for a in Array(1.0, 2.0) for b in BadIterMethod(1.0) if b > 0)
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_genexpr_later_clause_terminating_next_ships_a_terminate():
    def inline_form(gate):
        if gate > 0:
            return sum(a + b for a in Array(1.0, 2.0) for b in BadNextIterator(1.0))
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_while_condition_of_terminating_call_ships_a_terminate():
    # The loop is abandoned in its header, so its frame has to be closed the way visit_For's is; the pinned
    # nested variants of that closing live in test_terminating_next above.
    def inline_form(gate):
        if gate > 0:
            while bad_vec(-1).x > 0:
                pass
            return 1.0
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_match_pattern_reading_a_terminating_property_is_rejected():
    # A read the pattern itself performs can terminate only through a user property or __len__. Contexts
    # opened inside the pattern may be live with no continuation left for them, so this is rejected rather
    # than traced on.
    def fn():
        match BadPropValue(Mem.gate):
            case BadPropValue(p=1.0):
                return 1.0
        return 3.0

    with pytest.raises(CompilationError, match="terminates on every path is not supported inside a match pattern"):
        run_compiled(fn, runtime_checks=RuntimeChecks.NONE)


def test_match_guard_of_terminating_call_keeps_the_earlier_case():
    # The earlier case carries the live path, and it falls out of the match rather than returning from it, so
    # what the terminating guard has to preserve is a continuation this shape actually reads. Returning from
    # the earlier case instead pins nothing: the return is already emitted by then, so dropping the match's
    # continuation is unobservable. A bare `case _ if bad_vec(-1).x > 0:` alone terminates at both gates, and
    # an `and`-gated guard never reaches the guard's own resume because the `and` absorbs the dead context
    # first.
    def inline_form(gate):
        total = 2.0
        match gate:
            case 0.0:
                total = 3.0
            case _ if bad_vec(-1).x > 0:
                total = 1.0
        return total

    assert run_gated(inline_form, 0.0) == (1.0, 3.0)
    mark, _ = run_gated(inline_form, 2.0)
    assert mark == 0.0


def test_assert_message_of_terminating_call_compiles_with_checks_disabled():
    # run_compiled at NONE: with checks disabled the assert is stripped and its message is traced in a
    # disconnected context, so the mark oracle cannot discriminate here. The pin is that the shape compiles
    # and that none of the message's effects ship: a shipped terminate would return 0 instead of 3.
    def fn():
        assert Mem.gate > 1, str(bad_vec(-1).x)
        return 3.0

    assert run_compiled(fn, runtime_checks=RuntimeChecks.NONE) == 3


def test_assert_message_of_terminating_call_keeps_the_passing_path():
    # With checks on, the failing branch evaluates the message, which terminates on its own: all that branch
    # was going to do anyway. The resume this pins is the assertion-passes context.
    def inline_form(gate):
        assert gate > 1, str(bad_vec(-1).x)
        return 3.0

    assert run_gated(inline_form, 2.0, runtime_checks=RuntimeChecks.TERMINATE) == (1.0, 3.0)
    mark, _ = run_gated(inline_form, 0.0, runtime_checks=RuntimeChecks.TERMINATE)
    assert mark == 0.0


def test_conditional_expression_false_arm_of_terminating_call_keeps_the_true_arm():
    # The mirror of the true-arm test above.
    def inline_form(gate):
        if gate > 0:
            return 4.0 if gate > 5 else bad_vec(-1).x
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    assert run_gated(inline_form, 6.0) == (1.0, 4.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_conditional_expression_with_both_arms_terminating_ships_a_terminate():
    # Both arms terminate, so there is no result to name and the merge is dead; the gate's own false arm
    # stays live.
    def inline_form(gate):
        if gate > 0:
            return bad_vec(-1).x if gate > 5 else bad_vec(-2).x
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0
    mark, _ = run_gated(inline_form, 6.0)
    assert mark == 0.0


def test_body_terminating_at_every_gate_never_falls_through():
    # No gate reaches the fall-through, so the caller gets the placeholder with a dead context rather than a
    # value. This is the shape that pins the consumer contract at the meta_fn boundary: run_gated above has
    # to check `ctx().live` before storing the result, and both gates must still ship the terminate.
    def inline_form(gate):
        if gate > 0:
            return bad_vec(-1).x
        return bad_vec(-2).x

    for gate in (0.0, 1.0):
        mark, _ = run_gated(inline_form, gate)
        assert mark == 0.0


def yields_from_bad_iter_dunder():
    yield from BadIterMethod(1.0)


def yields_from_bad_next():
    yield from BadNextIterator(1.0)


def test_yield_from_terminating_iter_dunder_ships_a_terminate():
    # The delegate constructs fine and its `__iter__` terminates: distinct from the pinned test above, where
    # the delegated expression itself is the terminating call.
    def inline_form(gate):
        if gate > 0:
            total = 0.0
            for v in yields_from_bad_iter_dunder():
                total += v
            return total
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_yield_from_terminating_next_ships_a_terminate():
    def inline_form(gate):
        if gate > 0:
            total = 0.0
            for v in yields_from_bad_next():
                total += v
            return total
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_call_with_terminating_keyword_argument_ships_a_terminate():
    # A constant out-of-range Array index terminates the context from inside a meta_fn. The remaining
    # arguments are still traced, into the dead context, and the check before the call is what notices.
    def inline_form(gate):
        def f(a=0.0, b=0.0):
            return a + b

        if gate > 0:
            return f(a=Array(1.0, 2.0)[5], b=1.0)
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_call_with_terminating_last_argument_ships_a_terminate():
    # As above with the terminating argument last, which the same check notices: this pins that the trailing
    # position is not special.
    def inline_form(gate):
        def f(a, b=0.0):
            return a + b

        if gate > 0:
            return f(1.0, b=Array(1.0, 2.0)[5])
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_terminating_decorator_expression_ships_a_terminate():
    # The decorator value is applied by calling it, so a decorator expression that terminates would otherwise
    # be called as a placeholder. There is no function to define here, and the terminate still has to ship.
    def inline_form(gate):
        if gate > 0:

            @bad_vec(-1).x
            def inner():
                return 1.0

            return 2.0
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_terminating_decorator_application_stops_applying_outer_decorators():
    def inline_form(gate):
        def terminating(decorated):
            _ = bad_vec(-1).x
            return decorated

        if gate > 0:

            @fail_if_applied_as_decorator
            @terminating
            def inner():
                return 1.0

            return inner()
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_call_with_terminating_double_star_operand_ships_a_terminate():
    # The `**` operand is the one argument position whose value the call builder inspects, to merge it into
    # the keyword mapping, so it needs the liveness check the plain positions do not.
    def inline_form(gate):
        def f(a=0.0):
            return a

        if gate > 0:
            return f(**bad_mapping(-1))
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0


def test_boolop_or_over_terminating_call_keeps_the_short_circuit_path():
    # The mirror of the `and` test above: the left-true path is the live sibling of the branch the
    # terminating right operand dies in.
    def inline_form(gate):
        if gate > 0:
            if (gate > 5) or bad_vec(-1).x > 0:
                return 1.0
            return 2.0
        return 5.0

    assert run_gated(inline_form, 0.0) == (1.0, 5.0)
    assert run_gated(inline_form, 6.0) == (1.0, 1.0)
    mark, _ = run_gated(inline_form, 1.0)
    assert mark == 0.0
