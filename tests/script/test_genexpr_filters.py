"""Tests that a compile-time-false `if` filter stops the rest of a generator expression's clause chain.

A filter that folds to false skips the element, so nothing after it in the same clause chain runs: not a later
filter, not a later `for`, not the element expression. The iterable here is an `Array`, which the compiler
iterates at runtime; a tuple would take the compile-time arm instead, which already skips correctly.
"""

from sonolus.script.array import Array
from sonolus.script.debug import debug_log
from tests.script.conftest import run_and_validate

# A feature flag read as a compile-time constant, the realistic spelling of a filter that folds to false.
SHOW_ALL = False


def _is_positive(x: float) -> bool:
    return x > 0.0


def _logged(x: float) -> float:
    debug_log(x)
    return x


def test_false_filter_before_a_call_filter():
    def fn():
        return sum(x for x in Array(1.0, 2.0, 3.0) if SHOW_ALL if _is_positive(x))

    assert run_and_validate(fn) == 0


def test_false_filter_after_a_runtime_filter():
    def fn():
        return sum(x for x in Array(1.0, 2.0, 3.0) if x > 0.0 if SHOW_ALL if _is_positive(x))

    assert run_and_validate(fn) == 0


def test_false_filter_before_a_second_for_clause():
    def fn():
        return sum(x * y for x in Array(1.0, 2.0, 3.0) if SHOW_ALL for y in Array(10.0, 20.0))

    assert run_and_validate(fn) == 0


def test_false_filter_before_a_second_for_clause_with_its_own_filter():
    def fn():
        return sum(x * y for x in Array(1.0, 2.0, 3.0) if SHOW_ALL for y in Array(10.0, 20.0) if y > 10.0)

    assert run_and_validate(fn) == 0


def test_false_filter_with_a_call_in_the_element_expression():
    # The element expression is the third position that must be skipped, and nothing may be logged from it:
    # run_and_validate compares the interpreter's log against plain Python's entry for entry.
    def fn():
        return sum(_logged(x) for x in Array(1.0, 2.0, 3.0) if SHOW_ALL)

    assert run_and_validate(fn) == 0


def test_false_filter_with_a_nested_genexpr_in_the_element_expression():
    def fn():
        return sum(sum(y for y in Array(1.0, 2.0)) for x in Array(1.0, 2.0, 3.0) if SHOW_ALL)

    assert run_and_validate(fn) == 0


def test_false_filter_consumed_by_a_for_loop():
    def fn():
        total = 0.0
        for x in (v for v in Array(1.0, 2.0, 3.0) if SHOW_ALL if _is_positive(v)):
            total += x
        return total

    assert run_and_validate(fn) == 0.0


def test_false_filter_alone_still_yields_nothing():
    def fn():
        return sum(x for x in Array(1.0, 2.0, 3.0) if SHOW_ALL)

    assert run_and_validate(fn) == 0


def test_false_filter_before_a_comparison_filter():
    def fn():
        return sum(x for x in Array(1.0, 2.0, 3.0) if SHOW_ALL if x > 1.0)

    assert run_and_validate(fn) == 0


def test_false_filter_on_the_second_for_clause():
    def fn():
        return sum(x * y for x in Array(1.0, 2.0, 3.0) for y in Array(10.0, 20.0) if SHOW_ALL)

    assert run_and_validate(fn) == 0


def test_true_filter_before_a_call_filter_still_runs_the_chain():
    def fn():
        return sum(_logged(x) for x in Array(1.0, 2.0, 3.0) if not SHOW_ALL if _is_positive(x))

    assert run_and_validate(fn) == 6.0


def test_runtime_filter_before_a_call_filter_still_runs_the_chain():
    def fn():
        return sum(x for x in Array(1.0, 2.0, 3.0) if x > 1.0 if _is_positive(x))

    assert run_and_validate(fn) == 5.0
