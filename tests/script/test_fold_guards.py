"""Tests that a compile-time fold which cannot produce a value defers to the runtime op instead of aborting.

The tracer visits both sides of every runtime branch, so a fold that raises rejects programs that never reach
the offending call. `black_box()` below is always true at runtime and opaque to the optimizer, so the guarded
expression is dead in plain Python while the compiler still traces it.
"""

import math
import random

import pytest

from tests.script.conftest import compile_fn, run_and_validate


def black_box():
    # Copied from tests/script/test_flow.py: always true at runtime, opaque to the optimizer.
    return random.randrange(0, 1) == 0


def _log_of_negative():
    if not black_box():
        return math.log(-1.0)
    return 1.0


def _log_of_zero_with_base():
    if not black_box():
        return math.log(0.0, 2.0)  # ruff: ignore[redundant-log-base]
    return 1.0


def _asin_out_of_domain():
    if not black_box():
        return math.asin(2.0)
    return 1.0


def _sinh_overflow():
    if not black_box():
        return math.sinh(1000.0)
    return 1.0


def _floor_of_infinity():
    if not black_box():
        return math.floor(math.inf)
    return 1.0


def _int_of_infinity():
    if not black_box():
        return int(math.inf)
    return 1.0


@pytest.mark.parametrize(
    "fn",
    [
        _log_of_negative,
        _log_of_zero_with_base,
        _asin_out_of_domain,
        _sinh_overflow,
        _floor_of_infinity,
        _int_of_infinity,
    ],
    ids=["log-negative", "log-zero-with-base", "asin", "sinh", "floor-inf", "int-inf"],
)
def test_out_of_domain_constant_on_a_runtime_branch_does_not_abort_the_compile(fn):
    assert run_and_validate(fn) == 1.0


def test_acos_just_outside_the_domain_compiles():
    # A compile-time dot product of unit vectors lands at 1.0000000000000002 for many directions, and acos has
    # no answer there, so the fold cannot supply one. Nothing raises at runtime, so the compile must still
    # succeed; compile_fn rather than run_and_validate because CPython's acos raises on the same value.
    def fn():
        return math.acos(1.0000000000000002)

    compile_fn(fn)


def _dead_branch_multiply_overflow():
    if not black_box():
        return 1e200 * 1e200
    return 1.0


def _dead_branch_add_overflow():
    if not black_box():
        return 1.7e308 + 1.7e308
    return 1.0


def _dead_branch_subtract_overflow():
    if not black_box():
        return 1.7e308 - -1.7e308
    return 1.0


@pytest.mark.parametrize(
    "fn",
    [_dead_branch_multiply_overflow, _dead_branch_add_overflow, _dead_branch_subtract_overflow],
    ids=["multiply", "add", "subtract"],
)
def test_overflowing_fold_on_a_runtime_branch_does_not_abort_the_compile(fn):
    assert run_and_validate(fn) == 1.0


def test_overflowing_multiply_chain_compiles():
    # Every literal here is inside the f32 range the docs promise, and CPython evaluates the chain to inf, so
    # refusing to compile it is wrong. Only compilation is asserted: the intermediates run to about 1e300, far
    # outside that range, and no value is promised there.
    def fn():
        return 1e38 * 1e38 * 1e38 * 1e38 * 1e38 * 1e38 * 1e38 * 1e38 * 1e38

    compile_fn(fn)
