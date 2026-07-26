# ruff: noqa
"""Test cases intended to cover more complex control flow.

PYTEST_DONT_REWRITE
"""

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import Box
from sonolus.script.debug import debug_log
from sonolus.script.internal.context import RuntimeChecks
from sonolus.script.internal.error import CompilationError
from sonolus.script.vec import Vec2
from tests.script.conftest import run_compiled
from tests.script.conftest import run_and_validate
from tests.script.test_record import Pair

ints = st.integers(min_value=-999999, max_value=999999)
small_ints = st.integers(min_value=-2, max_value=2)
floats = st.floats(min_value=-999999, max_value=999999, allow_infinity=False, allow_nan=False)
divisor_floats = floats.filter(lambda x: abs(x) > 1e-6)


def test_loop_with_side_effects():
    def fn():
        for i in range(10):
            # This can't be inlined at all since it would change the behavior
            x = debug_log(i)
            if i % 2 == 0:
                debug_log(x)
                # We can inline this, but we need to remove the original statement
                y = debug_log(i)
                debug_log(y)

    run_and_validate(fn)


def test_nested_loops_with_breaks():
    def fn():
        for i in range(5):
            debug_log(i)
            for j in range(5):
                x = debug_log(j)
                if x > 2:
                    break
                debug_log(x * i)
            debug_log(-i)

    run_and_validate(fn)


def test_conditional_assignments():
    def fn():
        x = 0
        for i in range(5):
            # Test conditional assignments with side effects
            x = debug_log(i) if i % 2 == 0 else debug_log(-i)
            debug_log(x)

            # Test multiple branches with side effects
            if i > 2:
                y = debug_log(i * 2)
            elif i > 1:
                y = debug_log(i * 3)
            else:
                y = debug_log(i * 4)
            debug_log(y)

    run_and_validate(fn)


def test_loop_with_continue():
    def fn():
        for i in range(10):
            x = debug_log(i)
            if i % 3 == 0:
                continue
            debug_log(x)
            if i % 2 == 0:
                y = debug_log(i * 2)
                debug_log(y)

    run_and_validate(fn)


def test_nested_conditionals():
    def fn():
        for i in range(5):
            x = debug_log(i)
            if i > 0:
                if i > 2:
                    y = debug_log(x * 2)
                    if i > 3:
                        debug_log(y * 2)
                    debug_log(y)
                debug_log(x)
            debug_log(-i)

    run_and_validate(fn)


def test_variable_reassignment():
    def fn():
        x = debug_log(0)
        for i in range(5):
            debug_log(x)
            x = debug_log(i)  # Reassign x with side effect
            if i % 2 == 0:
                x = debug_log(x * 2)  # Reassign again
            debug_log(x)

    run_and_validate(fn)


def test_early_returns():
    def fn():
        for i in range(10):
            x = debug_log(i)
            if i > 5:
                debug_log(-1)
                return
            debug_log(x)
            if i % 2 == 0:
                y = debug_log(i * 2)
                debug_log(y)

    run_and_validate(fn)


def test_loop_variable_dependencies():
    def fn():
        prev = debug_log(0)
        curr = debug_log(1)
        for i in range(5):
            debug_log(prev)
            debug_log(curr)
            temp = debug_log(curr)
            curr = debug_log(prev + curr)
            prev = debug_log(temp)
            debug_log(i)

    run_and_validate(fn)


def test_pair_mutations_in_loop():
    def fn():
        p = Pair(0, 0)
        for i in range(5):
            # Test mutation of first field with side effects
            p.first = debug_log(i)
            debug_log(p.first)

            # Test mutation of second field in conditional
            if i % 2 == 0:
                p.second = debug_log(i * 2)
                debug_log(p.second)

            # Test reading after mutation
            debug_log(p.first + p.second)
        return p

    run_and_validate(fn)


def test_pair_copy_from_operator():
    def fn():
        p = Pair(0, 0)
        for i in range(5):
            debug_log(p.first)
            # Test copy-from with side effects in constructor
            p @= Pair(debug_log(i), debug_log(i * 2))
            debug_log(p.second)

            if i % 2 == 0:
                # Test nested copy-from operations
                temp = Pair(debug_log(i * 3), debug_log(i * 4))
                p @= temp
                debug_log(p.first)
        return p

    run_and_validate(fn)


def test_pair_conditional_mutations():
    def fn():
        p = Pair(0, 0)
        for i in range(5):
            # Test conditional mutations with side effects
            p.first = debug_log(i) if i % 2 == 0 else debug_log(-i)
            debug_log(p.first)

            # Test multiple mutation branches
            if i > 2:
                p.second = debug_log(i * 2)
            elif i > 1:
                p.second = debug_log(i * 3)
            else:
                p @= Pair(debug_log(i), debug_log(-i))
            debug_log(p.second)
        return p

    run_and_validate(fn)


def test_pair_nested_mutations():
    def fn():
        p1 = Pair(0, 0)
        p2 = Pair(1, 1)
        for i in range(5):
            debug_log(p1.first)
            if i % 2 == 0:
                # Test interleaved mutations between two pairs
                p1.first = debug_log(i)
                p2.second = debug_log(i * 2)
                p1 @= p2
                debug_log(p1.second)
            else:
                # Test copy followed by mutation
                p2 @= p1
                p2.first = debug_log(-i)
                debug_log(p2.first)
            debug_log(p1.first + p2.second)
        return p1

    run_and_validate(fn)


def test_pair_early_return_with_mutations():
    def fn():
        p = Pair(0, 0)
        for i in range(10):
            p.first = debug_log(i)
            if i > 5:
                p.second = debug_log(-1)
                debug_log(p.second)
                return p
            debug_log(p.first)
            if i % 2 == 0:
                p @= Pair(debug_log(i * 2), debug_log(i * 3))
                debug_log(p.first)
        return p

    run_and_validate(fn)


def test_random_multi_use():
    def add(a, b):
        return a + b

    # Random has no side effects, but is impure, so we need to test that optimizations don't break it.
    def fn():
        a = random.uniform(1, 10)
        b = add(a, Pair(a, a).first)
        c = add(b, -2 * a)
        return c == 0

    for _ in range(100):
        run_and_validate(fn)


def test_switch_with_integer_cases():
    def fn():
        for i in range(5):
            debug_log(i)
            match i:
                case 0:
                    debug_log(0)
                case 1:
                    debug_log(11)
                case 2:
                    debug_log(22)
                case 3:
                    debug_log(33)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_integer_cases_and_default():
    def fn():
        for i in range(5):
            debug_log(i)
            match i:
                case 0:
                    debug_log(0)
                case 1:
                    debug_log(11)
                case 2:
                    debug_log(22)
                case 3:
                    debug_log(33)
                case _:
                    debug_log(-1)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_offset_integer_cases():
    def fn():
        for i in range(10):
            debug_log(i)
            match i:
                case 1:
                    debug_log(0)
                case 2:
                    debug_log(11)
                case 3:
                    debug_log(22)
                case 4:
                    debug_log(33)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_offset_integer_cases_and_default():
    def fn():
        for i in range(10):
            debug_log(i)
            match i:
                case 1:
                    debug_log(0)
                case 2:
                    debug_log(11)
                case 3:
                    debug_log(22)
                case 4:
                    debug_log(33)
                case _:
                    debug_log(-1)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_integer_cases_and_stride():
    def fn():
        for i in range(10):
            debug_log(i)
            match i:
                case 0:
                    debug_log(0)
                case 2:
                    debug_log(11)
                case 4:
                    debug_log(22)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_integer_cases_and_stride_and_default():
    def fn():
        for i in range(10):
            debug_log(i)
            match i:
                case 0:
                    debug_log(0)
                case 2:
                    debug_log(11)
                case 4:
                    debug_log(22)
                case _:
                    debug_log(-1)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_integer_cases_and_stride_and_offset():
    def fn():
        for i in range(10):
            debug_log(i)
            match i:
                case 1:
                    debug_log(0)
                case 3:
                    debug_log(11)
                case 5:
                    debug_log(22)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_integer_cases_and_stride_and_offset_and_default():
    def fn():
        for i in range(10):
            debug_log(i)
            match i:
                case 1:
                    debug_log(0)
                case 3:
                    debug_log(11)
                case 5:
                    debug_log(22)
                case _:
                    debug_log(-1)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_integer_cases_and_variable_stride():
    def fn():
        for i in range(10):
            debug_log(i)
            match i:
                case 0:
                    debug_log(0)
                case 2:
                    debug_log(11)
                case 5:
                    debug_log(22)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_integer_cases_and_variable_stride_and_default():
    def fn():
        for i in range(10):
            debug_log(i)
            match i:
                case 0:
                    debug_log(0)
                case 2:
                    debug_log(11)
                case 5:
                    debug_log(22)
                case _:
                    debug_log(-1)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_integer_cases_and_variable_stride_and_offset():
    def fn():
        for i in range(10):
            debug_log(i)
            match i:
                case 1:
                    debug_log(0)
                case 3:
                    debug_log(11)
                case 6:
                    debug_log(22)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_integer_cases_and_variable_stride_and_offset_and_default():
    def fn():
        for i in range(10):
            debug_log(i)
            match i:
                case 1:
                    debug_log(0)
                case 3:
                    debug_log(11)
                case 6:
                    debug_log(22)
                case _:
                    debug_log(-1)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_float_cases():
    def fn():
        for i in range(10):
            debug_log(i)
            match i / 2:
                case 0.0:
                    debug_log(0)
                case 0.5:
                    debug_log(11)
                case 1.0:
                    debug_log(22)
                case 3.0:
                    debug_log(33)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_float_cases_and_default():
    def fn():
        for i in range(5):
            debug_log(i)
            match i / 2:
                case 0.0:
                    debug_log(0)
                case 0.5:
                    debug_log(11)
                case 1.0:
                    debug_log(22)
                case 1.5:
                    debug_log(33)
                case _:
                    debug_log(-1)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_out_of_order_integer_cases():
    def fn():
        for i in range(5):
            debug_log(i)
            match i:
                case 2:
                    debug_log(0)
                case 0:
                    debug_log(11)
                case 3:
                    debug_log(22)
                case 1:
                    debug_log(33)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_out_of_order_integer_cases_and_default():
    def fn():
        for i in range(5):
            debug_log(i)
            match i:
                case 2:
                    debug_log(0)
                case 0:
                    debug_log(11)
                case 3:
                    debug_log(22)
                case 1:
                    debug_log(33)
                case _:
                    debug_log(-1)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_out_of_order_float_cases():
    def fn():
        for i in range(5):
            debug_log(i)
            match i / 2:
                case 1.0:
                    debug_log(0)
                case 0.0:
                    debug_log(11)
                case 1.5:
                    debug_log(22)
                case 0.5:
                    debug_log(33)
            debug_log(i)

    run_and_validate(fn)


def test_switch_with_out_of_order_float_cases_and_default():
    def fn():
        for i in range(5):
            debug_log(i)
            match i / 2:
                case 1.0:
                    debug_log(0)
                case 0.0:
                    debug_log(11)
                case 1.5:
                    debug_log(22)
                case 0.5:
                    debug_log(33)
                case _:
                    debug_log(-1)
            debug_log(i)

    run_and_validate(fn)


def test_switch_multiple_blank_edges_box():
    # Tests a bug where multiple blank edges cause conversion out of SSA to be wrong.
    def fn():
        for i in range(6):
            debug_log(i)
            x = Box(123)
            match i:
                case 0:
                    x @= Box(0)
                case 1:
                    x @= Box(1)
                case 2:
                    x @= Box(2)
                case 3:
                    pass
                case 4:
                    x @= Box(4)
            # In the bug, the default branch and case 3 both become edges directly to the end of the match,
            # but the default assignment of Box(123) to x ends up only happening along one of those edges
            # nondeterministically, so when the other branch is taken, an uninitialized x is used.
            debug_log(x.value)

    run_and_validate(fn)


def test_while_else_taken():
    def fn():
        i = 0
        while i < 5:
            debug_log(i)
            i += 1
        debug_log(-1)

    run_and_validate(fn)


def test_while_else_not_taken():
    def fn():
        i = 0
        while i < 5:
            debug_log(i)
            i += 1
            if i == 3:
                break
        else:
            debug_log(-1)

    run_and_validate(fn)


def test_for_else_taken():
    def fn():
        for i in range(5):
            debug_log(i)
        debug_log(-1)

    run_and_validate(fn)


def test_for_else_not_taken():
    def fn():
        for i in range(5):
            debug_log(i)
            if i == 3:
                break
        else:
            debug_log(-1)

    run_and_validate(fn)


def test_for_else_over_tuple():
    # The tuple form is unrolled rather than compiled as a loop, so it needs its own coverage.
    def fn():
        for i in (1, 2, 3):
            debug_log(i)
        else:
            debug_log(-1)

    run_and_validate(fn)


def test_for_else_over_empty_tuple():
    def fn():
        for i in ():
            debug_log(i)
        else:
            debug_log(-1)

    run_and_validate(fn)


def test_for_else_over_tuple_with_break():
    def fn():
        for i in (1, 2, 3):
            debug_log(i)
            if i == 2:
                break
        else:
            debug_log(-1)

    run_and_validate(fn)


def black_box():
    # This really always returns True, but the optimizer doesn't know that,
    # so we can use it as a black box to prevent branches from being optimized away.
    return random.randrange(0, 1) == 0


def black_box_value(v: float | int) -> float | int:
    if black_box():
        return v
    return 0


def black_box_log(v: float | int) -> float | int:
    debug_log(v)
    return black_box_value(v)


def test_error_if_conflicting_definitions():
    def fn():
        x = Pair(1, 2)
        if black_box():
            x = Pair(3, 4)
        debug_log(x.first)

    with pytest.raises(CompilationError, match="'x' has multiple conflicting definitions"):
        run_compiled(fn)


def test_error_while_conflicting_definitions():
    def fn():
        x = Pair(1, 2)
        while black_box():
            debug_log(x.first)
            x = Pair(3, 4)
        return 1

    with pytest.raises(CompilationError, match="'x' may have conflicting definitions between loop iterations"):
        run_compiled(fn)


def test_error_for_conflicting_definitions():
    def fn():
        x = Pair(1, 2)
        for _ in range(5):
            debug_log(x.first)
            x = Pair(3, 4)
        return 1

    with pytest.raises(CompilationError, match="'x' may have conflicting definitions between loop iterations"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_behind_single_predecessor_merge():
    # The read-before-rebind hazard must still be caught when the read sits after a
    # merge point with only one live predecessor (the early return makes the true
    # branch dead at the join). Such merges reuse the predecessor context directly,
    # and the loop-variable read counts must survive that reuse.
    def fn():
        x = Pair(1, 2)
        while black_box():
            if black_box():
                return 0
            debug_log(x.first)
            x = Pair(3, 4)
        return 1

    with pytest.raises(CompilationError, match="'x' may have conflicting definitions between loop iterations"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_behind_multi_predecessor_merge():
    # Same hazard behind a merge with two live predecessors: the merge must keep the
    # loop-variable binding object (and its read counts) rather than rebuilding it,
    # or the read after the join never reaches the back-edge conflict check and the
    # loop silently reads the pre-loop reference.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 2:
            i += 1
            if black_box():
                debug_log(0)
            debug_log(x.first)
            x = Pair(3, 4)
        return 1

    with pytest.raises(CompilationError, match="'x' may have conflicting definitions between loop iterations"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_behind_distinct_binding_merge():
    # Same hazard behind a merge whose live predecessors hold distinct binding objects over
    # the same value: rebinding x to the pre-loop alias y mints a fresh binding wrapping the
    # value the loop header already holds, while the fallthrough path keeps the header's own
    # binding. The merge of those must keep the read that follows it visible to the header,
    # or the back-edge check never fires and the loop silently reads the pre-loop reference
    # on later iterations.
    def fn():
        x = Pair(1, 2)
        y = x
        i = 0
        while i < 2:
            i += 1
            if black_box():
                x = y
            debug_log(x.first)
            x = Pair(3, 4)
        return 1

    with pytest.raises(CompilationError, match="'x' may have conflicting definitions between loop iterations"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_behind_distinct_binding_merge_in_else_branch():
    # The same shape with the rebinding arm second, so the header's binding is the merge's
    # first source rather than its second. Which source carries the header's binding must not
    # matter.
    def fn():
        x = Pair(1, 2)
        y = x
        i = 0
        while i < 2:
            i += 1
            if black_box():
                debug_log(0)
            else:
                x = y
            debug_log(x.first)
            x = Pair(3, 4)
        return 1

    with pytest.raises(CompilationError, match="'x' may have conflicting definitions between loop iterations"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_behind_nested_distinct_binding_merges():
    # Two merges stack: the inner join rebuilds the binding once and the outer join rebuilds
    # that result again, so the read is two merges removed from the header's binding. The
    # check has to follow the whole chain, not just one link of it.
    def fn():
        x = Pair(1, 2)
        y = x
        i = 0
        while i < 2:
            i += 1
            if black_box():
                if black_box():
                    x = y
            debug_log(x.first)
            x = Pair(3, 4)
        return 1

    with pytest.raises(CompilationError, match="'x' may have conflicting definitions between loop iterations"):
        run_compiled(fn)


def test_no_error_when_read_follows_rebind_in_the_same_branch():
    # The read sits after the rebind in the arm that rebound x, so it reads that arm's value
    # and never the header's own reference. The merge really does discard a read here, and
    # discarding it is correct: attributing a source's reads to the header would reject this.
    def fn():
        x = Pair(1, 2)
        y = x
        i = 0
        while i < 2:
            i += 1
            if black_box():
                x = y
                debug_log(x.first)
            else:
                debug_log(0)
            x = Pair(3, 4)
        return 1

    assert run_compiled(fn) == 1


def test_no_error_when_every_path_rebinds_loop_variable_before_read():
    # Every path rebinds x before the read, so neither merge source is the header's binding
    # and the read is of the rebound value. This must keep compiling.
    def fn():
        x = Pair(1, 2)
        y = x
        i = 0
        while i < 2:
            i += 1
            if black_box():
                x = y
                debug_log(1)
            else:
                x = y
                debug_log(2)
            debug_log(x.first)
            x = Pair(3, 4)
        return 1

    assert run_compiled(fn) == 1


def test_no_error_when_merged_loop_variable_is_never_read():
    # The same merge as the failing cases with no read of x in the body at all: rebinding it
    # before the back edge is harmless, so the check must stay quiet.
    def fn():
        x = Pair(1, 2)
        y = x
        i = 0
        while i < 2:
            i += 1
            if black_box():
                x = y
            x = Pair(3, 4)
        return 1

    assert run_compiled(fn) == 1


def test_error_while_conflicting_definitions_across_nested_loop_header():
    # The inner loop writes x, so the inner loop header mints its own binding over the value the
    # outer header holds. The read inside the inner loop lands on that binding, and unless the
    # outer header's binding can reach it the outer back edge sees no read at all: the loop
    # silently reads the pre-loop reference on every iteration.
    def fn():
        x = Pair(1, 2)
        y = x
        i = 0
        while i < 3:
            i += 1
            j = 0
            while j < 1:
                j += 1
                debug_log(x.first)
                x = y
            x = Pair(3, 4)
        return 1

    with pytest.raises(CompilationError, match="'x' may have conflicting definitions between loop iterations"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_across_nested_for_loop_header():
    # The same shape with a for loop inside. Both loop forms mint the inner header's binding
    # through the same path, and nothing else pins that they do.
    def fn():
        x = Pair(1, 2)
        y = x
        i = 0
        while i < 3:
            i += 1
            for _ in range(1):
                debug_log(x.first)
                x = y
            x = Pair(3, 4)
        return 1

    with pytest.raises(CompilationError, match="'x' may have conflicting definitions between loop iterations"):
        run_compiled(fn)


def test_no_error_when_outer_loop_rebinds_before_a_nested_loop_reads():
    # The loop analogue of test_no_error_when_read_follows_rebind_in_the_same_branch: the outer
    # loop rebinds x before entering the inner one, so the inner header's binding descends from
    # that rebind rather than from the outer header's binding, and the read is of the rebound
    # value. Whatever links the two headers must be taken from the binding live at inner-loop
    # entry, or this program is rejected for no reason.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 3:
            i += 1
            x = Pair(3, 4)
            y = x
            j = 0
            while j < 1:
                j += 1
                debug_log(x.first)
                x = y
        return 1

    assert run_and_validate(fn) == 1


def test_error_while_conflicting_definitions_when_read_follows_a_rebinding_continue():
    # The continue reaches the header before the read below it is traced, so a check made once
    # per back edge cannot see that read: the loop compiles against the pre-loop reference
    # and logs 1 where plain Python logs 3.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 3:
            i += 1
            if i == 1:
                x = Pair(3, 4)
                continue
            debug_log(x.first)
        return 1

    with pytest.raises(CompilationError, match="'x' may have conflicting definitions between loop iterations"):
        run_compiled(fn)


def test_error_for_conflicting_definitions_when_read_follows_a_rebinding_continue():
    # The same ordering hazard in a for loop, which closes its header at its own site.
    def fn():
        x = Pair(1, 2)
        for i in range(3):
            if i == 0:
                x = Pair(3, 4)
                continue
            debug_log(x.first)
        return 1

    with pytest.raises(CompilationError, match="'x' may have conflicting definitions between loop iterations"):
        run_compiled(fn)


def test_error_while_true_conflicting_definitions_when_read_follows_a_rebinding_continue():
    # A statically true test closes the loop at a third site, reached by neither test above.
    def fn():
        x = Pair(1, 2)
        i = 0
        while True:
            i += 1
            if i > 3:
                break
            if i == 1:
                x = Pair(3, 4)
                continue
            debug_log(x.first)
        return 1

    with pytest.raises(CompilationError, match="'x' may have conflicting definitions between loop iterations"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_when_read_follows_a_continue_that_does_not_rebind():
    # The regression guard for the pair above: here the read really is a read, reached through a
    # continue that leaves x alone, and the fallthrough rebinds. Deferring the check must not
    # lose this, and neither must dropping the back edge's own lookup of x.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 3:
            i += 1
            if i == 1:
                continue
            debug_log(x.first)
            x = Pair(3, 4)
        return 1

    with pytest.raises(CompilationError, match="'x' may have conflicting definitions between loop iterations"):
        run_compiled(fn)


def test_no_error_when_loop_variable_is_rebound_but_never_read():
    # x is never read anywhere, so rebinding it on the way back to the header carries no hazard.
    # The only thing that ever touched this binding's read count is the check's own lookup of x
    # on the continue edge, which is not a read the program performs.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 3:
            i += 1
            if i == 1:
                continue
            x = Pair(3, 4)
        return 1

    assert run_and_validate(fn) == 1


def test_no_error_when_loop_variable_is_rebound_before_a_continue_and_never_read():
    # The mirror image of the test above, with the rebind on the continue arm instead. The two
    # differ only in which arm rebinds, so they have to agree.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 3:
            i += 1
            if i == 1:
                x = Pair(3, 4)
                continue
        return 1

    assert run_and_validate(fn) == 1


def test_no_error_when_merged_loop_variable_is_rebound_but_never_read():
    # The same shape as the two above with a merge in front of it, so the check's own lookup
    # lands on a binding the header can reach through the merge rather than on the header's
    # binding itself. x is still never read: nothing here is a hazard.
    def fn():
        x = Pair(1, 2)
        y = x
        i = 0
        while i < 3:
            i += 1
            if black_box():
                x = y
            if i == 1:
                continue
            x = Pair(3, 4)
        return 1

    assert run_and_validate(fn) == 1


def test_error_while_conflicting_definitions_when_read_is_in_a_nested_loop_after_a_rebinding_continue():
    # Both hazards at once, and it needs both fixes: the read is inside an inner loop that
    # rebinds x, so only a link between the two headers makes it visible, and it is traced after
    # the continue's back edge, so only a check deferred to loop close is still listening.
    def fn():
        x = Pair(1, 2)
        y = x
        i = 0
        while i < 3:
            i += 1
            if i == 1:
                x = Pair(3, 4)
                continue
            j = 0
            while j < 1:
                j += 1
                debug_log(x.first)
                x = y
        return 1

    with pytest.raises(CompilationError, match="'x' may have conflicting definitions between loop iterations"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_when_a_rebound_reference_is_read_after_the_loop():
    # The loop's exit is branched off the header before the body is traced, so it still names the
    # object the loop was entered with and whatever the body rebound is lost on the way out: this
    # logs 1 where plain Python logs 3. Adding a break already errors here, because that exit goes
    # through a merge that yields a conflict; the fallthrough exit has to match it.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 3:
            i += 1
            x = Pair(3, 4)
        debug_log(x.first)
        return 1

    with pytest.raises(CompilationError, match="'x' has multiple conflicting definitions"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_when_a_rebinding_continue_is_read_after_the_loop():
    # The same hazard with a continue in front of the rebind. Nothing about the continue matters to
    # the exit path, and the pair pins that: whatever makes one conflicting has to do the same to the other.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 3:
            i += 1
            if i == 1:
                continue
            x = Pair(3, 4)
        debug_log(x.first)
        return 1

    with pytest.raises(CompilationError, match="'x' has multiple conflicting definitions"):
        run_compiled(fn)


def test_error_for_conflicting_definitions_when_a_rebound_reference_is_read_after_the_loop():
    # The for loop builds its header and its exit at its own site, reached by neither test above.
    def fn():
        x = Pair(1, 2)
        for _ in range(3):
            x = Pair(3, 4)
        debug_log(x.first)
        return 1

    with pytest.raises(CompilationError, match="'x' has multiple conflicting definitions"):
        run_compiled(fn)


def test_error_while_true_conflicting_definitions_when_a_break_precedes_the_rebind():
    # A statically true test has no fallthrough exit, so every way out is a break, and this break
    # sits before the rebind and so still names the pre-loop object. One live exit means the merge
    # after the loop returns that context unchanged and never mints a conflict of its own, which is
    # why handling only the fallthrough exit would leave this shape silently wrong.
    def fn():
        x = Pair(1, 2)
        i = 0
        while True:
            i += 1
            if i > 3:
                break
            x = Pair(3, 4)
        debug_log(x.first)
        return 1

    with pytest.raises(CompilationError, match="'x' has multiple conflicting definitions"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_when_a_nested_loop_rebinds_and_the_outer_reads_after():
    # The inner loop makes its own exit conflicting, and the outer body continues from it. That has to
    # survive being carried through the enclosing loop rather than being re-minted clean.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 3:
            i += 1
            j = 0
            while j < 2:
                j += 1
                x = Pair(3, 4)
            debug_log(x.first)
        return 1

    with pytest.raises(CompilationError, match="'x' has multiple conflicting definitions"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_when_the_read_is_in_the_else_clause():
    # The else clause is traced in the exit context itself, so this is the most direct expression of
    # the hazard, and it pins that the exit is made conflicting before the clause is visited rather than after.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 3:
            i += 1
            x = Pair(3, 4)
        else:
            debug_log(x.first)
        return 1

    with pytest.raises(CompilationError, match="'x' has multiple conflicting definitions"):
        run_compiled(fn)


def test_error_for_conflicting_definitions_when_the_read_is_in_the_else_clause():
    # The same for the for loop, whose else clause runs in its own exit context.
    def fn():
        x = Pair(1, 2)
        for _ in range(3):
            x = Pair(3, 4)
        else:
            debug_log(x.first)
        return 1

    with pytest.raises(CompilationError, match="'x' has multiple conflicting definitions"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_when_the_exit_carries_a_merged_binding():
    # Rebinding x to the pre-loop alias y in one arm makes the merge below the if mint a fresh
    # binding over the value the header already holds, so the break carries a descendant of the
    # header's binding rather than the binding itself. That has to follow those edges.
    def fn():
        x = Pair(1, 2)
        y = x
        i = 0
        while True:
            i += 1
            if i == 1:
                x = y
            if i > 3:
                break
            x = Pair(3, 4)
        debug_log(x.first)
        return 1

    with pytest.raises(CompilationError, match="'x' has multiple conflicting definitions"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_when_a_rebound_reference_is_returned():
    # Returning the reference reads it through the same conflicting exit as a debug_log does.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 3:
            i += 1
            x = Pair(3, 4)
        return x.first

    with pytest.raises(CompilationError, match="'x' has multiple conflicting definitions"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_when_a_closure_reads_after_the_loop():
    # A closure resolves a name from the enclosing function's scope through a different path than a
    # plain load does, and it has to see the conflicting binding too.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 3:
            i += 1
            x = Pair(3, 4)

        def inner():
            return x.first

        debug_log(inner())
        return 1

    with pytest.raises(CompilationError, match="'x' has multiple conflicting definitions"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_when_a_later_loop_reads_a_rebound_reference():
    # A second loop over the same name prepares its own header from the conflicting binding, so this
    # pins that the conflicting binding survives prepare_loop_header rather than being re-bound clean.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 3:
            i += 1
            x = Pair(3, 4)
        j = 0
        while j < 2:
            j += 1
            debug_log(x.first)
        return 1

    with pytest.raises(CompilationError, match="'x' has multiple conflicting definitions"):
        run_compiled(fn)


def test_error_while_conflicting_definitions_when_a_rebound_array_is_read_after_the_loop():
    # Every reference type is in this family, not only Record: the guard is on whether the type is a
    # value type, so one non-Record case pins that it is not written against Record.
    def fn():
        x = Array(1, 2)
        i = 0
        while i < 3:
            i += 1
            x = Array(3, 4)
        debug_log(x[0])
        return 1

    with pytest.raises(CompilationError, match="'x' has multiple conflicting definitions"):
        run_compiled(fn)


def test_no_error_when_a_break_after_the_rebind_carries_the_new_reference():
    # This break really does hold the re-bound object, which is what Python leaves too, so it must
    # be skipped. Making every exit conflicting unconditionally would reject this.
    def fn():
        x = Pair(1, 2)
        i = 0
        while True:
            i += 1
            x = Pair(3, 4)
            if i > 3:
                break
        debug_log(x.first)
        return 1

    assert run_and_validate(fn) == 1


def test_no_error_when_a_break_rebinds_to_the_pre_loop_reference():
    # The break assigns x the pre-loop object outright, so it names a fresh binding that happens to
    # hold the value the header holds, and the program is correct. This is what makes the conflicting
    # set a question of which binding an exit names rather than which value it holds: a rule written
    # on the value would reject this, and a rule written on the binding cannot, because an unlinked
    # binding over that value can only have come from a real assignment on this path.
    def fn():
        x = Pair(1, 2)
        y = x
        i = 0
        while True:
            i += 1
            x = Pair(3, 4)
            if i > 3:
                x = y
                break
        debug_log(x.first)
        return 1

    assert run_and_validate(fn) == 1


def test_no_error_when_a_nested_loop_rebinds_a_reference_that_is_never_read():
    # The inner loop makes x conflicting, and the outer back edge then finds a conflicting binding where it
    # expects the outer header's object. It has to read that as a rebind rather than raise on it:
    # nothing reads x anywhere, so there is no hazard to report.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 3:
            i += 1
            for _ in range(2):
                x = Pair(3, 4)
        return 1

    assert run_and_validate(fn) == 1


def test_no_error_when_a_conditionally_rebound_reference_is_never_read():
    # Only one arm rebinds x, so the back edge carries a conflicting binding, and nothing ever reads
    # it. Valid Python, and the back edge must not reject it on the strength of the conflict alone.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 3:
            i += 1
            if i == 1:
                x = Pair(3, 4)
        return 1

    assert run_and_validate(fn) == 1


def test_no_error_when_rebound_to_the_same_reference_and_read_after_the_loop():
    # The back edge names the object the header holds, so nothing conflicts and no exit is made conflicting.
    def fn():
        x = Pair(1, 2)
        y = x
        i = 0
        while i < 3:
            i += 1
            x = y
        debug_log(x.first)
        return 1

    assert run_and_validate(fn) == 1


def test_no_error_when_the_body_restores_the_loop_reference_before_the_back_edge():
    # x is rebound and then restored, so by the back edge it names the header's object again.
    def fn():
        x = Pair(1, 2)
        y = x
        i = 0
        while i < 3:
            i += 1
            x = Pair(3, 4)
            x = y
        debug_log(x.first)
        return 1

    assert run_and_validate(fn) == 1


def test_no_error_when_a_value_type_loop_variable_is_read_after_the_loop():
    # A value type merges through a slot the header allocates, so it is never a rebind candidate and
    # no exit of a loop that only writes value types is ever made conflicting.
    def fn():
        x = 1
        i = 0
        while i < 3:
            i += 1
            x = 3
        debug_log(x)
        for _ in range(2):
            x = x + 1
        debug_log(x)
        return 1

    assert run_and_validate(fn) == 1


def test_no_error_when_an_unrolled_for_rebinds_a_reference():
    # Iterating a tuple unrolls into straight-line code with no header and no back edge, so it
    # reproduces Python exactly and must stay outside this mechanism entirely.
    def fn():
        x = Pair(1, 2)
        for _ in (0, 1, 2):
            x = Pair(3, 4)
        debug_log(x.first)
        return 1

    assert run_and_validate(fn) == 1


def test_no_error_when_a_rebound_reference_is_reassigned_before_the_read():
    # Assigning after the loop replaces the conflicting binding, so the read that follows is fine.
    def fn():
        x = Pair(1, 2)
        i = 0
        while i < 3:
            i += 1
            x = Pair(3, 4)
        x = Pair(5, 6)
        debug_log(x.first)
        return 1

    assert run_and_validate(fn) == 1


def test_walrus_operator():
    def fn():
        x: int = 0
        while (y := x) < 5:
            debug_log(y)
            x += 1

    run_and_validate(fn)


def test_match_singletons():
    def m(x):
        match x:
            case None:
                return 0
            case _:
                return 1

    def fn():
        return Array(m(None), m(0))

    assert run_and_validate(fn) == Array(0, 1)


def test_match_true_not_supported():
    def m(x):
        match x:
            case True:
                return 0
            case _:
                return 1

    def fn():
        m(True)
        return 1

    with pytest.raises(CompilationError, match="not supported"):
        run_compiled(fn)


def test_match_false_not_supported():
    def m(x):
        match x:
            case False:
                return 0
            case _:
                return 1

    def fn():
        m(False)
        return 1

    with pytest.raises(CompilationError, match="not supported"):
        run_compiled(fn)


def test_match_int_not_supported():
    def m(x):
        match x:
            case int():
                return 0

    def fn():
        m(1)
        return 1

    with pytest.raises(CompilationError, match="not supported"):
        run_compiled(fn)


@given(small_ints, small_ints, small_ints)
def test_and(x, y, z):
    def fn():
        a = x and y and z
        b = black_box_log(x) and y and z
        c = x and black_box_log(y) and z
        d = x and y and black_box_log(z)
        e = black_box_log(x) and black_box_log(y) and z
        f = black_box_log(x) and y and black_box_log(z)
        g = x and black_box_log(y) and black_box_log(z)
        h = black_box_log(x) and black_box_log(y) and black_box_log(z)
        return Array(a, b, c, d, e, f, g, h)

    assert run_and_validate(fn) == Array(*(x and y and z for _ in range(8)))


@given(small_ints, small_ints, small_ints)
def test_or(x, y, z):
    def fn():
        a = x or y or z
        b = black_box_log(x) or y or z
        c = x or black_box_log(y) or z
        d = x or y or black_box_log(z)
        e = black_box_log(x) or black_box_log(y) or z
        f = black_box_log(x) or y or black_box_log(z)
        g = x or black_box_log(y) or black_box_log(z)
        h = black_box_log(x) or black_box_log(y) or black_box_log(z)
        return Array(a, b, c, d, e, f, g, h)

    assert run_and_validate(fn) == Array(*(x or y or z for _ in range(8)))


@given(small_ints, small_ints, small_ints)
def test_and_or(x, y, z):
    def fn():
        a = x and y or z
        b = black_box_log(x) and y or z
        c = x and black_box_log(y) or z
        d = x and y or black_box_log(z)
        e = black_box_log(x) and black_box_log(y) or z
        f = black_box_log(x) and y or black_box_log(z)
        g = x and black_box_log(y) or black_box_log(z)
        h = black_box_log(x) and black_box_log(y) or black_box_log(z)
        return Array(a, b, c, d, e, f, g, h)

    assert run_and_validate(fn) == Array(*(x and y or z for _ in range(8)))


@given(small_ints, small_ints, small_ints)
def test_or_and(x, y, z):
    def fn():
        a = x or y and z
        b = black_box_log(x) or y and z
        c = x or black_box_log(y) and z
        d = x or y and black_box_log(z)
        e = black_box_log(x) or black_box_log(y) and z
        f = black_box_log(x) or y and black_box_log(z)
        g = x or black_box_log(y) and black_box_log(z)
        h = black_box_log(x) or black_box_log(y) and black_box_log(z)
        return Array(a, b, c, d, e, f, g, h)

    assert run_and_validate(fn) == Array(*(x or y and z for _ in range(8)))


@given(small_ints, small_ints, small_ints)
def test_chained_comparison(x, y, z):
    def fn():
        a = x < y < z
        b = black_box_log(x) < y < z
        c = x < black_box_log(y) < z
        d = x < y < black_box_log(z)
        e = black_box_log(x) < black_box_log(y) < z
        f = black_box_log(x) < y < black_box_log(z)
        g = x < black_box_log(y) < black_box_log(z)
        h = black_box_log(x) < black_box_log(y) < black_box_log(z)
        return Array(a, b, c, d, e, f, g, h)

    assert run_and_validate(fn) == Array(*(x < y < z for _ in range(8)))


def test_while_true():
    def fn():
        debug_log(1)
        while True:
            debug_log(2)
            break
        else:
            debug_log(3)
        debug_log(4)

    run_and_validate(fn)


def test_while_false():
    def fn():
        debug_log(1)
        while False:
            debug_log(2)
        else:
            debug_log(3)
        debug_log(4)

    run_and_validate(fn)


def test_for_empty():
    def fn():
        debug_log(1)
        for _ in zip():
            debug_log(2)
        else:
            debug_log(3)
        debug_log(4)

    run_and_validate(fn)


def test_loop_with_aug_assign():
    def fn():
        a = 0
        b = 0
        while a < 5:
            debug_log(a + b)
            b = a
            a += 1

    for _ in range(100):
        run_and_validate(fn)


def test_break_in_nested_for_else():
    def fn():
        for _ in range(2):
            for _ in range(2):
                debug_log(1)
            else:
                debug_log(2)
                break
            debug_log(3)
        else:
            debug_log(4)
        debug_log(5)

    run_and_validate(fn)


def test_loop_redefinition_of_reference_type():
    def fn():
        x = Vec2(1, 2)
        for i in range(10):
            x = Vec2(3, 4)
            debug_log(x.x + x.y)

    run_and_validate(fn)


def test_loop_redefinition_of_reference_type_with_invalid_read():
    def fn():
        x = Vec2(1, 2)
        for i in range(10):
            debug_log(x.x + x.y)
            x = Vec2(3, 4)
            debug_log(x.x + x.y)

    with pytest.raises(CompilationError, match="'x' may have conflicting definitions between loop iterations"):
        run_compiled(fn)


def test_bare_annotation_is_a_noop():
    def fn():
        x: int
        x = 5
        y: float = 2.5
        return x + y

    assert run_and_validate(fn) == 7.5


def test_bare_annotation_does_not_rebind_existing_value():
    def fn():
        x = 3
        x: int
        return x

    assert run_and_validate(fn) == 3


def test_bare_annotation_for_name_never_assigned():
    def fn():
        x: int
        return 1

    assert run_and_validate(fn) == 1


def test_bare_annotation_does_not_bind_name():
    # Per PEP 526 a bare annotation binds nothing, so reading the name is an error rather than reading
    # some placeholder value.
    def fn():
        x: int
        return x

    with pytest.raises(CompilationError, match="Name x is not defined"):
        run_compiled(fn)


_SHADOWED_GLOBAL = 5


def test_bare_annotation_shadows_a_global():
    # PEP 526 makes an annotated name local to the whole function, so the module-level value is not visible.
    def fn():
        _SHADOWED_GLOBAL: int
        return _SHADOWED_GLOBAL

    with pytest.raises(CompilationError, match="Name _SHADOWED_GLOBAL is not defined"):
        run_compiled(fn)


def test_bare_annotation_shadows_a_builtin():
    def fn():
        len: int  # noqa: A001
        return len((1, 2, 3))

    with pytest.raises(CompilationError, match="Name len is not defined"):
        run_compiled(fn)


def test_bare_annotation_shadows_for_the_whole_function():
    def fn():
        value = _SHADOWED_GLOBAL
        _SHADOWED_GLOBAL: int
        return value

    with pytest.raises(CompilationError, match="Name _SHADOWED_GLOBAL is not defined"):
        run_compiled(fn)


def test_parenthesized_bare_annotation_does_not_shadow():
    # `(x): int` is not a simple target, so unlike `x: int` it does not make the name local and the module-level
    # value stays visible.
    def fn():
        (_SHADOWED_GLOBAL): int
        return _SHADOWED_GLOBAL

    assert run_and_validate(fn) == 5


def test_bare_annotation_in_nested_function_does_not_shadow_outer():
    def fn():
        def inner():
            x: int
            return 1

        x = 5
        return x + inner()

    assert run_and_validate(fn) == 6


def test_bare_annotation_then_assigned_in_branches():
    def fn():
        n = 0
        for _ in range(3):
            n += 1
        total: int
        if n > 2:
            total = 10
        else:
            total = 20
        return total

    assert run_and_validate(fn) == 10


def test_bare_annotation_on_attribute_target_evaluates_primary():
    # `p.first: int` binds nothing, but CPython still evaluates the primary `p` for its side effects.
    def fn():
        def side(pair):
            debug_log(11)
            return pair

        p = Pair(4, 5)
        side(p).first: int
        return p.first

    assert run_and_validate(fn) == 4


def test_bare_annotation_on_subscript_target_evaluates_primary():
    def fn():
        def side(arr):
            debug_log(7)
            return arr

        a = Array(1, 2, 3)
        side(a)[0]: int
        return a[0]

    assert run_and_validate(fn) == 1


def test_bare_annotation_on_subscript_target_evaluates_index():
    # The index is evaluated too, after the primary and without the subscript itself being performed.
    def fn():
        def side_arr(arr):
            debug_log(7)
            return arr

        def side_idx(i):
            debug_log(13)
            return i

        a = Array(1, 2, 3)
        side_arr(a)[side_idx(1)]: int
        return a[1]

    assert run_and_validate(fn) == 2


def test_bare_annotation_on_nested_attribute_target_evaluates_primary():
    def fn():
        def side(pair):
            debug_log(11)
            return pair

        p = Pair(Pair(4, 5), 6)
        side(p).first.first: int
        return p.first.second

    assert run_and_validate(fn) == 5


def unsupported_msg():
    return [x for x in range(3)]


def test_assert_message_is_compiled_regardless_of_runtime_checks():
    # Under RuntimeChecks.NONE the assertion is stripped, so the message can never be evaluated, but it's still
    # compiled so that whether a program compiles doesn't depend on the runtime checks setting.
    def fn():
        n = 0
        for _ in range(3):
            n += 1
        assert n == 3, unsupported_msg()
        return n

    for runtime_checks in RuntimeChecks:
        with pytest.raises(CompilationError, match="List comprehensions are not supported"):
            run_compiled(fn, runtime_checks=runtime_checks)


def test_statically_true_assert_does_not_compile_its_message():
    # A statically passing assertion emits nothing at all, message included, so an unsupported construct in the
    # message is not reported.
    def fn():
        assert True, unsupported_msg()
        return 1

    for runtime_checks in RuntimeChecks:
        assert run_compiled(fn, runtime_checks=runtime_checks) == 1


def test_assert_message_side_effects_do_not_run_when_checks_are_disabled():
    # The message is compiled into a discarded context, so nothing it emits ends up on the straight-line path.
    # run_and_validate only compiles with checks disabled without running, so this runs it explicitly.
    def fn():
        def message():
            debug_log(99)
            return "failed"

        n = 0
        for _ in range(3):
            n += 1
        debug_log(1)
        assert n == 3, message()
        debug_log(2)
        return n

    assert run_and_validate(fn) == 3
    log = []
    assert run_compiled(fn, runtime_checks=RuntimeChecks.NONE, log_callback=log.append) == 3
    assert log == [1, 2]


def test_assert_message_bindings_do_not_escape_discarded_context():
    def fn():
        def message(value):
            return "failed" if value else "failed"

        n = 0
        for _ in range(3):
            n += 1
        x = 1
        assert n == 3, message(x := 2)

        # Python never evaluates the message here, so the walrus doesn't run and x is still 1.
        def inner():
            return x

        return inner() * 10 + n

    assert run_and_validate(fn) == 13
    assert run_compiled(fn, runtime_checks=RuntimeChecks.NONE) == 13
