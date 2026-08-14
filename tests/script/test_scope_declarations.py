"""Tests that `global` and `nonlocal` are rejected wherever they appear in a traced function.

CPython resolves both at compile time over the whole function body, so one in a region the compiler folds away
still decides where every assignment to that name writes. Rejecting only the ones the tracer reaches would let
such a declaration change the meaning of live code with no diagnostic.
"""

import pytest

from sonolus.script.internal.error import CompilationError
from tests.script.conftest import run_and_validate, run_compiled

# A feature flag read as a compile-time constant, the realistic spelling of a guard that folds to false.
ENABLED = False

_module_global = 1


def test_nonlocal_in_a_dead_branch_is_rejected():
    # run_compiled, not run_and_validate: plain Python accepts every shape in this file, so there is no
    # exception for the oracle to compare against.
    def fn():
        total = 0

        def bump():
            if False:
                nonlocal total
            total += 1
            return 0

        for _ in range(3):
            bump()
        return total

    with pytest.raises(CompilationError, match="Nonlocal statements are not supported"):
        run_compiled(fn)


def test_nonlocal_behind_a_constant_flag_is_rejected():
    def fn():
        x = 1

        def inner():
            if ENABLED:
                nonlocal x
            x = 2
            return 0

        inner()
        return x

    with pytest.raises(CompilationError, match="Nonlocal statements are not supported"):
        run_compiled(fn)


def test_nonlocal_in_the_else_of_a_constant_true_test_is_rejected():
    def fn():
        x = 1

        def inner():
            if True:
                pass
            else:
                nonlocal x
            x = 7
            return 0

        inner()
        return x

    with pytest.raises(CompilationError, match="Nonlocal statements are not supported"):
        run_compiled(fn)


def test_nonlocal_in_a_while_false_body_is_rejected():
    def fn():
        x = 1

        def inner():
            while False:
                nonlocal x
            x = 3
            return 0

        inner()
        return x

    with pytest.raises(CompilationError, match="Nonlocal statements are not supported"):
        run_compiled(fn)


def test_nonlocal_after_an_unconditional_break_is_rejected():
    def fn():
        x = 1

        def inner():
            while True:
                break
                nonlocal x
            x = 4
            return 0

        inner()
        return x

    with pytest.raises(CompilationError, match="Nonlocal statements are not supported"):
        run_compiled(fn)


def test_nonlocal_after_a_return_is_rejected():
    # CPython never reaches the assignment here either, so this shape did not diverge; it is rejected because
    # the declaration is part of the function, not because of where it sits.
    def fn():
        x = 1

        def inner():
            if True:
                return 0
            nonlocal x
            x = 2
            return 0

        inner()
        return x

    with pytest.raises(CompilationError, match="Nonlocal statements are not supported"):
        run_compiled(fn)


def test_nonlocal_on_a_live_path_is_still_rejected():
    def fn():
        x = 1

        def inner():
            nonlocal x
            x = 2
            return 0

        inner()
        return x

    with pytest.raises(CompilationError, match="Nonlocal statements are not supported"):
        run_compiled(fn)


def test_global_in_a_dead_branch_is_rejected():
    def fn():
        def inner():
            if False:
                global _module_global  # noqa: PLW0603
            _module_global = 5
            return 0

        inner()
        return _module_global

    with pytest.raises(CompilationError, match="Global statements are not supported"):
        run_compiled(fn)


def test_global_on_a_live_path_is_still_rejected():
    def fn():
        global _module_global  # noqa: PLW0603
        _module_global = 5
        return 0

    with pytest.raises(CompilationError, match="Global statements are not supported"):
        run_compiled(fn)


def test_a_nested_function_without_a_declaration_still_compiles():
    def fn():
        total = 1

        def bump():
            return total + 1

        return bump() + total

    assert run_and_validate(fn) == 3


def test_a_dead_branch_without_a_declaration_still_compiles():
    def fn():
        total = 1

        def bump():
            if False:
                total = 100  # noqa: F841
            return 2

        return bump() + total

    assert run_and_validate(fn) == 3
