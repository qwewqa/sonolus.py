"""Tests for the compile-time checks in sonolus.script.debug.

The checks are runtime_checks_enabled, static_assert, try_static_assert, assert_unreachable, is_static_true,
and is_static_false. These deliberately disagree between plain Python and a compiled build, so
run_and_validate cannot be the oracle: runtime_checks_enabled returns True unconditionally outside a compile
context but compiles to 0 under RuntimeChecks.NONE, and the is_static_* pair answers a question about the
trace that has no plain-Python meaning. The compiled behaviour therefore goes through run_compiled, and every
case whose result depends on the build pins one RuntimeChecks value, since run_compiled otherwise compares all
three against each other and rejects a disagreement. The three tests that call a predicate directly are
pinning the other side of that divergence: what it answers with no compile context to consult.

Each call site passes its own message so that the pytest.raises pattern names which check fired: the default
"Static assertion failed" belongs to both static_assert and try_static_assert, and is pinned once each.

PYTEST_DONT_REWRITE
"""

import pytest

from sonolus.script.debug import (
    assert_unreachable,
    is_static_false,
    is_static_true,
    runtime_checks_enabled,
    static_assert,
    try_static_assert,
)
from sonolus.script.internal.context import RuntimeChecks
from sonolus.script.internal.error import CompilationError
from tests.script.conftest import run_compiled


def runtime_sum() -> int:
    """A value the optimizer cannot fold: accumulated in a loop, so it is not a compile-time constant."""
    total = 0
    for i in range(1, 4):
        total += i
    return total


def test_runtime_checks_enabled_is_false_when_checks_are_off():
    def fn():
        return 1 if runtime_checks_enabled() else 0

    assert run_compiled(fn, runtime_checks=RuntimeChecks.NONE) == 0


def test_runtime_checks_enabled_is_true_when_checks_terminate():
    def fn():
        return 1 if runtime_checks_enabled() else 0

    assert run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE) == 1


def test_runtime_checks_enabled_is_true_when_checks_notify():
    def fn():
        return 1 if runtime_checks_enabled() else 0

    assert run_compiled(fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE) == 1


def test_runtime_checks_enabled_is_true_outside_a_compile_context():
    # The divergence that rules run_and_validate out: plain Python has no build to ask, so it reports True even
    # though the same call compiles to 0 under RuntimeChecks.NONE.
    assert runtime_checks_enabled() is True, "outside a compile context the check should report enabled"


def test_static_assert_accepts_a_statically_true_condition():
    def fn():
        static_assert(1 + 1 == 2, "sa-true")
        return 7

    assert run_compiled(fn) == 7


def test_static_assert_rejects_a_statically_false_condition():
    def fn():
        static_assert(1 + 1 == 3, "sa-false")
        return 7

    with pytest.raises(CompilationError, match="sa-false"):
        run_compiled(fn)


def test_static_assert_rejects_a_runtime_valued_condition():
    # The documented contract: static_assert fails whenever the condition is not a compile-time constant known
    # to be true, including when it is a runtime value that happens to hold.
    def fn():
        static_assert(runtime_sum() > 0, "sa-runtime")
        return 7

    with pytest.raises(CompilationError, match="sa-runtime"):
        run_compiled(fn)


def test_static_assert_reports_its_default_message():
    def fn():
        static_assert(1 + 1 == 3)
        return 7

    with pytest.raises(CompilationError, match="Static assertion failed"):
        run_compiled(fn)


def test_try_static_assert_accepts_a_statically_true_condition():
    def fn():
        try_static_assert(1 + 1 == 2, "tsa-true")
        return 7

    assert run_compiled(fn) == 7


def test_try_static_assert_rejects_a_statically_false_condition():
    def fn():
        try_static_assert(1 + 1 == 3, "tsa-false")
        return 7

    with pytest.raises(CompilationError, match="tsa-false"):
        run_compiled(fn)


def test_try_static_assert_reports_its_default_message():
    def fn():
        try_static_assert(1 + 1 == 3)
        return 7

    with pytest.raises(CompilationError, match="Static assertion failed"):
        run_compiled(fn)


def test_try_static_assert_falls_through_on_a_runtime_condition_that_holds():
    # Unlike static_assert, a condition that is not statically false compiles, and the runtime check it falls
    # back to leaves the callback alone when the condition turns out to be true.
    def fn():
        try_static_assert(runtime_sum() > 0, "tsa-runtime-holds")
        return 7

    assert run_compiled(fn) == 7


def test_try_static_assert_terminates_on_a_runtime_condition_that_fails():
    def fn():
        try_static_assert(runtime_sum() < 0, "tsa-runtime-fails")
        return 7

    logs = []
    # Pinned to one RuntimeChecks value because only the notify build logs, which run_compiled would otherwise
    # report as a disagreement between builds.
    result = run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE, log_callback=logs.append)

    assert result != 7, "the callback returned normally instead of terminating on the failed check"
    assert logs == [], "a terminate-only build should not log"


def test_try_static_assert_logs_once_before_terminating_in_a_notify_build():
    def fn():
        try_static_assert(runtime_sum() < 0, "tsa-runtime-fails-notify")
        return 7

    logs = []
    result = run_compiled(fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=logs.append)

    assert result != 7, "the callback returned normally instead of terminating on the failed check"
    # The message reaches the log as an index into the engine's message table, so only the count is asserted.
    assert len(logs) == 1, f"expected exactly one logged message, got {logs}"


def test_assert_unreachable_accepts_a_statically_dead_arm():
    def fn():
        x = 1
        if x == 1:
            return 7
        assert_unreachable("au-dead")
        return 0

    assert run_compiled(fn) == 7


def test_assert_unreachable_rejects_an_arm_the_compiler_traces():
    def fn():
        if runtime_sum() > 0:
            return 7
        assert_unreachable("au-traced")
        return 0

    with pytest.raises(CompilationError, match="au-traced"):
        run_compiled(fn)


def test_assert_unreachable_reports_its_default_message():
    def fn():
        if runtime_sum() > 0:
            return 7
        assert_unreachable()
        return 0

    with pytest.raises(CompilationError, match="Unreachable code reached"):
        run_compiled(fn)


def test_is_static_true_holds_for_a_compile_time_constant():
    def fn():
        return 1 if is_static_true(1 + 1 == 2) else 0

    assert run_compiled(fn) == 1


def test_is_static_true_fails_for_a_runtime_value_that_is_true():
    def fn():
        return 1 if is_static_true(runtime_sum() > 0) else 0

    assert run_compiled(fn) == 0


def test_is_static_false_holds_for_a_compile_time_constant():
    def fn():
        return 1 if is_static_false(1 + 1 == 3) else 0

    assert run_compiled(fn) == 1


def test_is_static_false_fails_for_a_runtime_value_that_is_false():
    def fn():
        return 1 if is_static_false(runtime_sum() < 0) else 0

    assert run_compiled(fn) == 0


def test_is_static_true_falls_back_to_truthiness_outside_a_compile_context():
    # With no trace to consult, every value is a constant, so the question degrades to plain truthiness.
    assert is_static_true(1) is True, "a truthy value should be statically true outside a compile context"
    assert is_static_true(0) is False, "a falsy value should not be statically true"


def test_is_static_false_falls_back_to_truthiness_outside_a_compile_context():
    assert is_static_false(0) is True, "a falsy value should be statically false outside a compile context"
    assert is_static_false(1) is False, "a truthy value should not be statically false"
