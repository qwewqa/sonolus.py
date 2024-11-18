"""Test cases intended to cover more complex control flow."""

from sonolus.script.debug import debug_log
from sonolus.script.random import random_float
from tests.script.conftest import validate_dual_run
from tests.script.test_record import Pair


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

    validate_dual_run(fn)


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

    validate_dual_run(fn)


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

    validate_dual_run(fn)


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

    validate_dual_run(fn)


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

    validate_dual_run(fn)


def test_variable_reassignment():
    def fn():
        x = debug_log(0)
        for i in range(5):
            debug_log(x)
            x = debug_log(i)  # Reassign x with side effect
            if i % 2 == 0:
                x = debug_log(x * 2)  # Reassign again
            debug_log(x)

    validate_dual_run(fn)


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

    validate_dual_run(fn)


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

    validate_dual_run(fn)


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

    validate_dual_run(fn)


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

    validate_dual_run(fn)


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

    validate_dual_run(fn)


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

    validate_dual_run(fn)


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

    validate_dual_run(fn)


def test_random():
    def add(a, b):  # noqa: FURB118
        return a + b

    # Random has no side effects, but is impure, so we need to test that optimizations don't break it.
    def fn():
        a = random_float(1, 10)
        b = add(a, Pair(a, a).first)
        c = add(b, -2 * a)
        return c == 0

    for _ in range(100):
        validate_dual_run(fn)
