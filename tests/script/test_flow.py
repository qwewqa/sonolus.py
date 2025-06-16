# ruff: noqa
"""Test cases intended to cover more complex control flow."""

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.debug import debug_log
from sonolus.script.internal.error import CompilationError
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

    with pytest.raises(CompilationError, match="conflicting definitions"):
        run_compiled(fn)


def test_error_while_conflicting_definitions():
    def fn():
        x = Pair(1, 2)
        while black_box():
            debug_log(x.first)
            x = Pair(3, 4)
        return 1

    with pytest.raises(CompilationError, match="conflicting definitions"):
        run_compiled(fn)


def test_error_for_conflicting_definitions():
    def fn():
        x = Pair(1, 2)
        for _ in range(5):
            debug_log(x.first)
            x = Pair(3, 4)
        return 1

    with pytest.raises(CompilationError, match="conflicting definitions"):
        run_compiled(fn)


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
