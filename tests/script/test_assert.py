"""Tests for assert statements.

PYTEST_DONT_REWRITE
"""

from sonolus.script.debug import assert_false, assert_true
from tests.script.conftest import run_compiled


def test_assertion_succeeds():
    def fn():
        assert True, "Message"
        return 1

    assert run_compiled(fn) == 1


def test_assertion_fails():
    def fn():
        assert False, "Message"  # noqa: B011, PT015
        # noinspection PyUnreachableCode
        return 1

    assert run_compiled(fn) == 0


def test_assert_true_succeeds():
    def fn():
        assert_true(True)
        return 1

    assert run_compiled(fn) == 1


def test_assert_true_fails():
    def fn():
        assert_true(False)
        return 1

    assert run_compiled(fn) == 0


def test_assert_false_succeeds():
    def fn():
        assert_false(False)
        return 1

    assert run_compiled(fn) == 1


def test_assert_false_fails():
    def fn():
        assert_false(True)
        return 1

    assert run_compiled(fn) == 0
