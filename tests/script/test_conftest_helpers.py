"""Tests for the conftest oracle helpers themselves.

The traced CFG depends on the callback and runtime_checks but not on the optimization level, so
run_and_validate and run_compiled trace each distinct (callback, runtime_checks) pair once and share the
CFG across the level loop. These tests pin that the trace count does not scale with the number of
optimization levels, and that every interpreter run still draws the same random stream.

run_and_validate also runs a leg that interns every closure value into engine ROM, which holds 32-bit
floats. The remaining tests pin the boundary of what that leg accepts and that a value past it is reported
as a harness failure naming the closure variable, including when the function under test raises on its own.
"""

import random

import pytest

from sonolus.backend.optimize import FAST_PASSES, MINIMAL_PASSES, STANDARD_PASSES
from sonolus.script.array import Array
from sonolus.script.debug import debug_log, error
from sonolus.script.internal.error import CompilationError
from tests.script import conftest as cf

ALL_LEVELS = [MINIMAL_PASSES, FAST_PASSES, STANDARD_PASSES]


def _traces_used(monkeypatch, levels, invoke):
    count = 0
    real_compile_fn = cf.compile_fn

    def counting_compile_fn(*args, **kwargs):
        nonlocal count
        count += 1
        return real_compile_fn(*args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(cf, "compile_fn", counting_compile_fn)
        m.setattr(cf, "optimization_levels", levels)
        invoke()
    return count


def test_run_and_validate_trace_count_does_not_scale_with_levels(monkeypatch):
    def fn():
        total = 0
        for i in range(5):
            total += i
        return total

    one_level = _traces_used(monkeypatch, [STANDARD_PASSES], lambda: cf.run_and_validate(fn))
    three_levels = _traces_used(monkeypatch, ALL_LEVELS, lambda: cf.run_and_validate(fn))
    assert one_level > 0
    assert three_levels == one_level


def test_run_compiled_trace_count_does_not_scale_with_levels(monkeypatch):
    def fn():
        total = 0
        for i in range(5):
            total += i
        return total

    one_level = _traces_used(monkeypatch, [STANDARD_PASSES], lambda: cf.run_compiled(fn))
    three_levels = _traces_used(monkeypatch, ALL_LEVELS, lambda: cf.run_compiled(fn))
    assert one_level > 0
    assert three_levels == one_level


def test_run_compiled_random_agrees_across_levels_and_checks():
    # run_compiled raises ValueError itself if the level or runtime-checks variants disagree, so the
    # assertion that matters is that the call returns at all: with the traces hoisted, the random state
    # must be restored before each interpreter run, not merely before each trace.
    def fn():
        total = 0
        for _i in range(10):
            total += random.randrange(0, 100)
        return total

    result = cf.run_compiled(fn)
    assert 0 <= result <= 990


def test_run_and_validate_reports_an_out_of_range_closure_value_as_a_harness_failure():
    magnitude = 1e39

    def fn():
        return magnitude

    with pytest.raises(RuntimeError, match=r"closure variable 'magnitude' \(value 1e\+39\)") as excinfo:
        cf.run_and_validate(fn)
    # CompilationError is a RuntimeError subclass, so match= alone would accept one, and run_and_validate
    # treats a CompilationError as a claim about the code under test.
    assert not isinstance(excinfo.value, CompilationError)


def test_run_and_validate_reports_the_harness_failure_even_when_the_function_itself_raises():
    magnitude = 1e39

    def fn():
        table = Array(1, 2, 3)
        _ = magnitude
        return table[7]  # raises IndexError as plain Python, so run_and_validate expects that exception

    # That expectation is compared against any CompilationError the compile legs raise, so routing the ROM
    # rejection through CompilationError would surface here as a message-less AssertionError.
    with pytest.raises(RuntimeError, match=r"closure variable 'magnitude' \(value 1e\+39\)"):
        cf.run_and_validate(fn)


def test_run_and_validate_accepts_a_closure_value_at_the_f32_maximum():
    # The largest finite 32-bit float, which engine ROM can hold exactly.
    magnitude = 3.4028234663852886e38

    def fn():
        return magnitude

    assert cf.run_and_validate(fn) == magnitude


def test_run_and_validate_runs_every_leg_before_reraising_a_python_exception(monkeypatch):
    marker = 7
    pass_count = 0
    real_run_passes = cf.run_passes

    def counting_run_passes(*args, **kwargs):
        nonlocal pass_count
        pass_count += 1
        return real_run_passes(*args, **kwargs)

    def fn():
        debug_log(marker)
        error("boom")

    monkeypatch.setattr(cf, "run_passes", counting_run_passes)
    monkeypatch.setattr(cf, "optimization_levels", ALL_LEVELS)

    with pytest.raises(RuntimeError, match="boom"):
        cf.run_and_validate(fn)
    assert pass_count == 2 * len(ALL_LEVELS)


def test_run_and_validate_checks_log_parity_when_python_raises(monkeypatch):
    real_run = cf.Interpreter.run

    def run_with_extra_log(self, *args, **kwargs):
        result = real_run(self, *args, **kwargs)
        self.log.append(99)
        return result

    def fn():
        debug_log(7)
        error("boom")

    monkeypatch.setattr(cf.Interpreter, "run", run_with_extra_log)
    monkeypatch.setattr(cf, "optimization_levels", [STANDARD_PASSES])

    with pytest.raises(AssertionError):
        cf.run_and_validate(fn)
