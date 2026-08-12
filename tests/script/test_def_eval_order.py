"""Tests for the evaluation order of a decorated `def` nested inside compiled code.

CPython evaluates decorator expressions top to bottom, then default expressions left to right, then builds the
function, then applies the decorators bottom to top. `run_and_validate` compares the compiled order against
CPython's own, so the expected order never has to be written down here.
"""

# A default expression with a side effect is what these tests observe, so B008 is suppressed for the module.
# ruff: noqa: B008

import inspect

import pytest

from sonolus.script.array import Array
from sonolus.script.debug import debug_log
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.meta_fn import meta_fn
from tests.script.conftest import compile_fn, run_and_validate


def identity_decorator(f):
    return f


def logging_decorator_expression(tag):
    """A decorator expression with a side effect: logs `tag` and yields a decorator that does nothing."""
    debug_log(tag)
    return identity_decorator


def logging_default(tag):
    """A default-value expression with a side effect: logs `tag` and yields 0."""
    debug_log(tag)
    return 0


def bump(counter):
    counter[0] += 1
    return counter[0]


def ignore_arg(_value):
    return identity_decorator


def test_decorator_expressions_and_defaults_evaluate_in_cpython_order():
    def fn():
        @logging_decorator_expression(1)
        @logging_decorator_expression(2)
        def inner(a=logging_default(3), b=logging_default(4)):
            return a + b

        return inner()

    assert run_and_validate(fn) == 0


def test_decorator_expressions_evaluate_top_down():
    def fn():
        @logging_decorator_expression(10)
        @logging_decorator_expression(20)
        def inner():
            return 5

        return inner()

    assert run_and_validate(fn) == 5


def test_decorator_expression_side_effect_is_visible_to_a_default():
    # No logging here: the divergence shows up in the returned value alone.
    def fn():
        counter = Array(0)

        @ignore_arg(bump(counter))
        def inner(x=bump(counter)):
            return x

        return inner()

    assert run_and_validate(fn) == 2


def test_decorators_apply_bottom_up():
    def add_one(f):
        def wrapped():
            return f() + 1

        return wrapped

    def double(f):
        def wrapped():
            return f() * 2

        return wrapped

    def fn():
        @add_one
        @double
        def inner():
            return 3

        return inner()

    assert run_and_validate(fn) == 7


def test_default_expressions_evaluate_left_to_right():
    def fn():
        def inner(a=logging_default(1), b=logging_default(2), *, c=logging_default(3)):
            return a + b + c

        return inner()

    assert run_and_validate(fn) == 0


def test_lambda_defaults_evaluate_left_to_right():
    def fn():
        f = lambda a=logging_default(1), b=logging_default(2): a + b  # noqa: E731
        return f()

    assert run_and_validate(fn) == 0


@meta_fn
def _failing_decorator_expression():
    raise ValueError("decorator expression failed")


def test_error_in_a_decorator_expression_is_reported_at_the_decorator():
    def fn():
        @_failing_decorator_expression()
        def inner(a=0):
            return a

        return inner()

    source_lines, first_line = inspect.getsourcelines(fn)
    marker = "@_failing_decorator_expression()"
    expected_line = first_line + next(i for i, line in enumerate(source_lines) if marker in line)

    with pytest.raises(CompilationError, match="decorator expression failed") as exc_info:
        compile_fn(fn)

    reported_lines = [
        frame.tb_frame.f_lineno
        for frame in _traceback_frames(exc_info.value)
        if frame.tb_frame.f_code.co_filename == __file__
    ]
    assert expected_line in reported_lines


def _traceback_frames(exception):
    while exception is not None:
        frame = exception.__traceback__
        while frame is not None:
            yield frame
            frame = frame.tb_next
        exception = exception.__cause__
