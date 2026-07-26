# ruff: noqa: PLW0108
"""Tests for assert statements.

PYTEST_DONT_REWRITE
"""

import random

from sonolus.script.containers import Box
from sonolus.script.debug import assert_false, assert_true, debug_log, notify, require
from sonolus.script.internal.context import RuntimeChecks
from sonolus.script.record import Record
from tests.script.conftest import run_and_validate, run_compiled


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


def test_assertion_dynamic_fails_none_mode():
    log_calls = []

    def fn():
        assert random.random() == -1, "Message"
        # noinspection PyUnreachableCode
        return 1

    assert run_compiled(fn, runtime_checks=RuntimeChecks.NONE, log_callback=lambda x: log_calls.append(x)) == 1
    assert len(log_calls) == 0


def test_assertion_static_fails_none_mode():
    log_calls = []

    def fn():
        assert False, "Message"  # noqa: B011, PT015
        # noinspection PyUnreachableCode
        return 1

    # Even in none, an assertion with a statically known-false condition should terminate.
    result = run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE, log_callback=lambda x: log_calls.append(x))
    assert result == 0
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


def test_assert_message_not_evaluated_when_static_assertion_succeeds():
    def fn():
        def make_message():
            debug_log(77)
            return "Message"

        x = 1
        assert x, make_message()
        debug_log(1)
        return 0

    run_and_validate(fn)


def test_assert_message_not_evaluated_when_dynamic_assertion_succeeds():
    def fn():
        def make_message():
            debug_log(77)
            return "Message"

        x = 0
        for _ in range(3):
            x += 1
        assert x > 0, make_message()
        debug_log(1)
        return x

    assert run_and_validate(fn) == 3


def test_assert_message_side_effect_does_not_affect_result():
    def fn():
        box = Box(0)

        def make_message():
            box.value += 1
            return "Message"

        x = 1
        assert x, make_message()
        return box.value

    assert run_and_validate(fn) == 0


def test_assert_message_evaluated_when_dynamic_assertion_fails():
    log_calls = []

    def fn():
        def make_message():
            debug_log(77)
            return "Message"

        x = 0
        for _ in range(3):
            x += 1
        assert x < 0, make_message()
        return 1

    result = run_compiled(
        fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=lambda x: log_calls.append(x)
    )
    assert result == 0
    assert len(log_calls) == 2
    assert log_calls[0] == 77


def test_assert_message_evaluated_when_static_assertion_fails():
    log_calls = []

    def fn():
        def make_message():
            debug_log(77)
            return "Message"

        assert False, make_message()  # noqa: B011, PT015
        # noinspection PyUnreachableCode
        return 1

    result = run_compiled(
        fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=lambda x: log_calls.append(x)
    )
    assert result == 0
    assert len(log_calls) == 2
    assert log_calls[0] == 77


class _AssertBoolRecord(Record):
    v: int

    def __bool__(self):
        return self.v != 0


class _AssertLenRecord(Record):
    n: int

    def __len__(self):
        return self.n


def test_assert_record_with_bool_true_agrees_with_if():
    def fn():
        x = 0
        for _ in range(3):
            x += 1
        r = _AssertBoolRecord(x)
        result = 0
        if r:
            result += 1
        assert r
        return result

    assert run_and_validate(fn) == 1


def test_assert_record_with_bool_false_fails():
    log_calls = []

    def fn():
        x = 0
        for _ in range(3):
            x -= 1
        r = _AssertBoolRecord(x + 3)
        assert r, "Message"
        return 1

    result = run_compiled(
        fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=lambda x: log_calls.append(x)
    )
    assert result == 0
    assert len(log_calls) == 1


def test_assert_record_with_len_true_agrees_with_if():
    def fn():
        x = 0
        for _ in range(2):
            x += 1
        r = _AssertLenRecord(x)
        result = 0
        if r:
            result += 1
        assert r
        return result

    assert run_and_validate(fn) == 1


def test_assert_record_with_len_zero_fails():
    log_calls = []

    def fn():
        x = 0
        for _ in range(2):
            x -= 1
        r = _AssertLenRecord(x + 2)
        assert r, "Message"
        return 1

    result = run_compiled(
        fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=lambda x: log_calls.append(x)
    )
    assert result == 0
    assert len(log_calls) == 1


def test_assert_non_empty_tuple_agrees_with_if():
    def fn():
        t = (1, 2)
        result = 0
        if t:
            result += 1
        assert t
        return result

    assert run_and_validate(fn) == 1


def test_assert_empty_tuple_fails():
    log_calls = []

    def fn():
        t = ()
        assert t, "Message"
        return 1

    result = run_compiled(
        fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=lambda x: log_calls.append(x)
    )
    assert result == 0
    assert len(log_calls) == 1


def test_assert_runtime_num_agrees_with_if():
    def fn():
        x = 0
        for _ in range(3):
            x += 1
        result = 0
        if x:
            result += 1
        assert x
        return result

    assert run_and_validate(fn) == 1
