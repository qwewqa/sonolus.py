# ruff: file-ignore[zip-without-explicit-strict]

import sys

import pytest

from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from sonolus.script.debug import debug_log
from sonolus.script.internal.context import RuntimeChecks
from sonolus.script.vec import Vec2
from tests.script.conftest import run_and_validate, run_compiled


def test_strict_zip_equal_lengths_matches_python():
    def fn():
        total = 0
        for a, b in zip(Array(1, 2), Array(10, 20), strict=True):
            total += a + b
        return total

    assert run_and_validate(fn) == 33


def test_strict_zip_single_iterable_matches_python():
    def fn():
        total = 0
        for (value,) in zip(Array(1, 2, 3), strict=True):
            total += value
        return total

    assert run_and_validate(fn) == 6


def test_strict_zip_compile_time_iterables_match_python():
    def fn():
        total = 0
        for a, b in zip((1, 2), (10, 20), strict=True):
            total += a + b
        return total

    assert run_and_validate(fn) == 33


@pytest.mark.parametrize(
    ("left", "right", "message"),
    [
        ((1,), (10, 20), r"zip\(\) argument 2 is longer than argument 1"),
        ((1, 2), (10,), r"zip\(\) argument 2 is shorter than argument 1"),
    ],
)
def test_strict_zip_compile_time_mismatch_terminates_when_consumed(left, right, message):
    def fn():
        for _ in zip(left, right, strict=True):
            pass
        return 0

    with pytest.raises(ValueError, match=message):
        run_and_validate(fn)


@pytest.mark.parametrize(
    ("iterables", "message"),
    [
        (((1, 2), (10, 20), (100,)), r"zip\(\) argument 3 is shorter than arguments 1-2"),
        (((1,), (10,), (100, 200)), r"zip\(\) argument 3 is longer than arguments 1-2"),
    ],
)
def test_strict_zip_three_compile_time_iterables_report_mismatch_when_consumed(iterables, message):
    def fn():
        for _ in zip(*iterables, strict=True):
            pass
        return 0

    with pytest.raises(ValueError, match=message):
        run_and_validate(fn)


@pytest.mark.parametrize(("left", "right"), [((1,), (10, 20)), ((1, 2), (10,))])
def test_strict_zip_compile_time_mismatch_after_break_is_not_reached(left, right):
    def fn():
        for a, b in zip(left, right, strict=True):
            return a + b
        return 0

    assert run_and_validate(fn) == 11


def test_strict_zip_compile_time_mismatch_in_runtime_unreached_branch():
    def fn():
        condition = 0
        for value in Array(0):
            condition += value
        if condition:
            for _ in zip((1,), (10, 20), strict=True):
                pass
        return 42

    assert run_and_validate(fn) == 42


@pytest.mark.parametrize("strict", [False, True])
def test_nested_non_strict_compile_time_zip_remains_sized(strict):
    def fn():
        runtime_false = Array(False)[0]
        inner_strict = runtime_false if strict else False
        total = 0
        for (a, b), c in zip(zip((1,), (2, 3), strict=inner_strict), (10,), strict=True):
            total += a + b + c
        return total

    assert run_and_validate(fn) == 13


def test_zip_with_no_iterables_is_empty_when_strict():
    def fn():
        total = 0
        for _ in zip(strict=True):
            total += 1
        return total

    assert run_and_validate(fn) == 0


def test_zip_explicit_non_strict_stops_at_shorter_iterable():
    def fn():
        total = 0
        for a, b in zip(Array(1), Array(10, 20), strict=False):
            total += a + b
        return total

    assert run_and_validate(fn) == 11


def test_zip_accepts_runtime_false_strict():
    def fn():
        strict = Array(False)[0]
        total = 0
        for a, b in zip(Array(1), Array(10, 20), strict=strict):
            total += a + b
        return total

    assert run_and_validate(fn) == 11


def test_zip_accepts_runtime_true_strict():
    def fn():
        strict = Array(True)[0]
        for _ in zip(Array(1), Array(10, 20), strict=strict):
            pass
        return 0

    with pytest.raises(ValueError, match=r"zip\(\) argument 2 is longer than argument 1"):
        run_and_validate(fn)


@pytest.mark.parametrize("strict", [False, True])
def test_compile_time_zip_accepts_runtime_strict(strict):
    def fn():
        runtime_strict = Array(strict)[0]
        total = 0
        for a, b in zip((1,), (10, 20), strict=runtime_strict):
            total += a + b
        return total

    if strict:
        with pytest.raises(ValueError, match=r"zip\(\) argument 2 is longer than argument 1"):
            run_and_validate(fn)
    else:
        assert run_and_validate(fn) == 11


def test_strict_zip_later_shorter_terminates_before_pulling_following_arm():
    def fn():
        def logged(value):
            debug_log(value)
            return value

        for _ in zip(
            map(logged, Array(1, 2)),
            map(logged, Array(10)),
            map(logged, Array(100, 200)),
            strict=True,
        ):
            pass
        return 0

    with pytest.raises(ValueError, match=r"zip\(\) argument 2 is shorter than argument 1"):
        run_and_validate(fn)


def test_strict_zip_last_shorter_after_complete_rows():
    def fn():
        for _ in zip(Array(1, 2), Array(10, 20), Array(100), strict=True):
            pass
        return 0

    with pytest.raises(ValueError, match=r"zip\(\) argument 3 is shorter than arguments 1-2"):
        run_and_validate(fn)


def test_strict_zip_first_shorter_probes_later_arms_in_order():
    def fn():
        def logged(value):
            debug_log(value)
            return value

        for _ in zip(
            Array[int, 0](),
            Array[int, 0](),
            map(logged, Array(30)),
            map(logged, Array(40)),
            strict=True,
        ):
            pass
        return 0

    with pytest.raises(ValueError, match=r"zip\(\) argument 3 is longer than arguments 1-2"):
        run_and_validate(fn)


def test_zip_does_not_advance_the_later_arm_after_a_short_arm():
    # Python pulls the left arm, finds it exhausted, and stops: the right arm is asked for four values in total,
    # never five.
    def fn():
        seen = VarArray[int, 16].new()

        def record(value):
            seen.append(value)
            return value

        total = 0
        for a, b in zip(map(record, Array(1, 2)), map(record, Array(10, 20, 30))):
            total += a + b
        return len(seen)

    assert run_and_validate(fn) == 4


def test_zip_does_not_resume_a_generator_arm_past_a_short_first_arm():
    # The generator has to be the second arm: as the first arm it is resumed on both paths, so it would not
    # distinguish anything.
    def fn():
        def gen():
            yield 10
            yield 20
            debug_log(99)

        total = 0
        for a, b in zip(Array(1, 2), gen()):
            total += a * b
        return total

    assert run_and_validate(fn) == 50


def test_zip_three_stops_at_the_middle_arm_without_advancing_the_last():
    # Six values over the two full rounds, plus the one the first arm gives before the middle arm runs out.
    def fn():
        seen = VarArray[int, 16].new()

        def record(value):
            seen.append(value)
            return value

        total = 0
        for a, b, c in zip(
            map(record, Array(1, 2, 3)),
            map(record, Array(10, 20)),
            map(record, Array(100, 200, 300)),
        ):
            total += a + b + c
        return len(seen)

    assert run_and_validate(fn) == 7


def test_map_over_two_iterables_stops_at_the_shorter_one():
    # map() with more than one iterable is built on zip(), so it inherits zip()'s pull order.
    def fn():
        seen = VarArray[int, 16].new()

        def record(value):
            seen.append(value)
            return value

        total = 0
        for value in map(lambda a, b: a + b, map(record, Array(1, 2)), map(record, Array(10, 20, 30))):
            total += value
        return total * 100 + len(seen)

    assert run_and_validate(fn) == 3304


@pytest.mark.skipif(sys.version_info < (3, 14), reason="map() strict requires Python 3.14")
@pytest.mark.parametrize("strict", [False, True])
def test_map_accepts_runtime_strict_for_equal_iterables(strict):
    def fn():
        runtime_strict = Array(strict)[0]
        return sum(map(lambda a, b: a + b, Array(1, 2), Array(10, 20), strict=runtime_strict))

    assert run_and_validate(fn) == 33


@pytest.mark.skipif(sys.version_info < (3, 14), reason="map() strict requires Python 3.14")
def test_map_accepts_runtime_false_strict_for_unequal_iterables():
    def fn():
        strict = Array(False)[0]
        return sum(map(lambda a, b: a + b, Array(1), Array(10, 20), strict=strict))

    assert run_and_validate(fn) == 11


@pytest.mark.skipif(sys.version_info < (3, 14), reason="map() strict requires Python 3.14")
def test_map_accepts_runtime_true_strict_for_unequal_iterables():
    def fn():
        strict = Array(True)[0]
        return sum(map(lambda a, b: a + b, Array(1), Array(10, 20), strict=strict))

    with pytest.raises(ValueError, match=r"map\(\) argument 2 is longer than argument 1"):
        run_and_validate(fn)


@pytest.mark.skipif(sys.version_info < (3, 14), reason="map() strict requires Python 3.14")
def test_map_compile_time_iterables_accept_runtime_strict():
    def fn():
        strict = Array(True)[0]
        return sum(map(lambda a, b: a + b, (1,), (10, 20), strict=strict))

    with pytest.raises(ValueError, match=r"map\(\) argument 2 is longer than argument 1"):
        run_and_validate(fn)


@pytest.mark.parametrize("use_map", [False, True])
def test_runtime_strict_terminates_with_runtime_checks_disabled(use_map):
    # run_compiled is intentional: this pins RuntimeChecks.NONE after every optimization level, which plain Python
    # cannot distinguish from the other compiled runtime-check modes.
    def fn():
        strict = Array(True)[0]
        if use_map:
            for _ in map(lambda a, b: a + b, Array(1), Array(10, 20), strict=strict):
                pass
        else:
            for _ in zip(Array(1), Array(10, 20), strict=strict):
                pass
        debug_log(99)
        return 0

    logs = []
    run_compiled(fn, runtime_checks=RuntimeChecks.NONE, log_callback=logs.append)
    assert logs == []


def test_zip_does_not_let_a_filter_arm_scan_past_a_short_arm():
    # A single extra pull on a filter arm consumes as many source elements as it takes to find a match, so this
    # case magnifies one skipped advance into several skipped predicate calls.
    def fn():
        def logged(value):
            debug_log(value)
            return value

        total = 0
        for a, b in zip(Array(1, 2), filter(lambda x: logged(x) % 2 == 0, Array(1, 2, 3, 4, 5, 6))):
            total += a + b
        return total

    assert run_and_validate(fn) == 9


def test_single_arm_zip_yields_one_tuples():
    # zip() with one iterable builds no Pair at all, so it is the base case of the chain walk.
    def fn():
        seen = VarArray[int, 8].new()

        def record(value):
            seen.append(value)
            return value

        total = 0
        for (value,) in zip(map(record, Array(1, 2, 3))):
            total += value
        return total * 10 + len(seen)

    assert run_and_validate(fn) == 63


def test_nested_zip_keeps_the_inner_tuple_nested():
    # The chain walk assembles the result tuple one arm at a time, so an arm that is itself a zip has to arrive as
    # one element rather than being spliced flat.
    def fn():
        total = 0
        for (a, b), c in zip(zip(Array(1, 2), Array(10, 20)), Array(100, 200)):
            total += a + b + c
        return total

    assert run_and_validate(fn) == 333


def test_enumerate_over_zipped_arrays_matches_python():
    def fn():
        total = 0
        for i, (a, b) in enumerate(zip(Array(1, 2, 3), Array(10, 20))):
            total += i * 100 + a * 10 + b
        return total

    assert run_and_validate(fn) == 160


def test_zip_writes_through_a_record_arm_in_first_position():
    # A record element is a reference, so a write through the loop variable has to land in the source array. Only
    # the first two elements are written: the zip stops once the second arm runs out.
    def fn():
        arr = Array(Vec2(1, 2), Vec2(3, 4), Vec2(5, 6))
        for v, n in zip(arr, Array(10, 20)):
            v.x = n
        return arr[0].x * 10000 + arr[1].x * 100 + arr[2].x

    assert run_and_validate(fn) == 102005


def test_zip_writes_through_a_record_arm_in_middle_position():
    # A middle arm is produced one branch deeper than the first one, so it exercises the same assembly step at a
    # different nesting depth.
    def fn():
        arr = Array(Vec2(1, 2), Vec2(3, 4), Vec2(5, 6))
        for n, v, m in zip(Array(1, 2), arr, Array(10, 20, 30)):
            v.x = n + m
        return arr[0].x * 10000 + arr[1].x * 100 + arr[2].x

    assert run_and_validate(fn) == 112205


def test_zip_writes_through_a_record_arm_in_last_position():
    # The last arm is the one that assembles the whole tuple, which is a different step of the chain walk than the
    # arms before it.
    def fn():
        arr = Array(Vec2(1, 2), Vec2(3, 4), Vec2(5, 6))
        for n, m, v in zip(Array(1, 2), Array(10, 20, 30), arr):
            v.y = n + m
        return arr[0].y * 10000 + arr[1].y * 100 + arr[2].y

    assert run_and_validate(fn) == 112206


def test_zip_writes_through_two_record_arms():
    def fn():
        a = Array(Vec2(1, 2), Vec2(3, 4))
        b = Array(Vec2(5, 6), Vec2(7, 8))
        for p, q in zip(a, b):
            p.x = q.x + 100
            q.y = p.y + 200
        # a is (105, 2), (107, 4) and b is (5, 202), (7, 204).
        return (a[0].x + a[1].x) * 10000 + b[0].y * 100 + b[1].y

    assert run_and_validate(fn) == 2140404
