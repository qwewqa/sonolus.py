# ruff: noqa: PLW0108
"""Tests for assert statements.

PYTEST_DONT_REWRITE
"""

from sonolus.script.debug import assert_false, assert_true, notify, require
from sonolus.script.internal.context import RuntimeChecks
from tests.script.conftest import run_compiled


def test_assertion_succeeds():
    log_calls = []

    def fn():
        assert True, "Message"
        return 1

    assert run_compiled(fn, log_callback=lambda x: log_calls.append(x)) == 1
    assert len(log_calls) == 0


def test_assertion_fails():
    log_calls = []

    def fn():
        assert False, "Message"  # noqa: B011, PT015
        # noinspection PyUnreachableCode
        return 1

    result = run_compiled(
        fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=lambda x: log_calls.append(x)
    )
    assert result == 0
    assert len(log_calls) == 1


def test_assert_true_succeeds():
    log_calls = []

    def fn():
        assert_true(True)
        return 1

    assert run_compiled(fn, log_callback=lambda x: log_calls.append(x)) == 1
    assert len(log_calls) == 0


def test_assert_true_fails():
    log_calls = []

    def fn():
        assert_true(False)
        return 1

    result = run_compiled(
        fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=lambda x: log_calls.append(x)
    )
    assert result == 0
    assert len(log_calls) == 1


def test_assert_false_succeeds():
    log_calls = []

    def fn():
        assert_false(False)
        return 1

    assert run_compiled(fn, log_callback=lambda x: log_calls.append(x)) == 1
    assert len(log_calls) == 0


def test_assert_false_fails():
    log_calls = []

    def fn():
        assert_false(True)
        return 1

    result = run_compiled(
        fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=lambda x: log_calls.append(x)
    )
    assert result == 0
    assert len(log_calls) == 1


def test_assertion_fails_terminate_mode():
    log_calls = []

    def fn():
        assert False, "Message"  # noqa: B011, PT015
        # noinspection PyUnreachableCode
        return 1

    result = run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE, log_callback=lambda x: log_calls.append(x))
    assert result == 0
    assert len(log_calls) == 0


def test_assert_true_fails_terminate_mode():
    log_calls = []

    def fn():
        assert_true(False)
        return 1

    result = run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE, log_callback=lambda x: log_calls.append(x))
    assert result == 0
    assert len(log_calls) == 0


def test_assert_false_fails_terminate_mode():
    log_calls = []

    def fn():
        assert_false(True)
        return 1

    result = run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE, log_callback=lambda x: log_calls.append(x))
    assert result == 0
    assert len(log_calls) == 0


def test_assertion_fails_none_mode():
    log_calls = []

    def fn():
        assert False, "Message"  # noqa: B011, PT015
        # noinspection PyUnreachableCode
        return 1

    assert run_compiled(fn, runtime_checks=RuntimeChecks.NONE, log_callback=lambda x: log_calls.append(x)) == 1
    assert len(log_calls) == 0


def test_notify_notify_and_terminate_mode():
    log_calls = []

    def fn():
        notify("Test notification")
        return 1

    assert (
        run_compiled(fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=lambda x: log_calls.append(x))
        == 1
    )
    assert len(log_calls) == 1


def test_notify_terminate_mode():
    log_calls = []

    def fn():
        notify("Test notification")
        return 1

    assert run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE, log_callback=lambda x: log_calls.append(x)) == 1
    assert len(log_calls) == 0


def test_notify_none_mode():
    log_calls = []

    def fn():
        notify("Test notification")
        return 1

    assert run_compiled(fn, runtime_checks=RuntimeChecks.NONE, log_callback=lambda x: log_calls.append(x)) == 1
    assert len(log_calls) == 0


def test_require_succeeds():
    log_calls = []

    def fn():
        require(True, "Should not fail")
        return 1

    assert run_compiled(fn, log_callback=lambda x: log_calls.append(x)) == 1
    assert len(log_calls) == 0


def test_require_fails():
    log_calls = []

    def fn():
        require(False, "Requirement failed")
        return 1

    result = run_compiled(
        fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=lambda x: log_calls.append(x)
    )
    assert result == 0
    assert len(log_calls) == 1


def test_require_fails_terminate_mode():
    log_calls = []

    def fn():
        require(False, "Requirement failed")
        return 1

    result = run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE, log_callback=lambda x: log_calls.append(x))
    assert result == 0
    assert len(log_calls) == 0


def test_require_fails_none_mode():
    log_calls = []

    def fn():
        require(False, "Requirement failed")
        return 1

    result = run_compiled(fn, runtime_checks=RuntimeChecks.NONE, log_callback=lambda x: log_calls.append(x))
    assert result == 0
    assert len(log_calls) == 0
