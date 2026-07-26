from enum import IntEnum

import pytest

from sonolus.script.array import Array
from sonolus.script.debug import debug_log
from sonolus.script.internal.error import CompilationError
from tests.script.conftest import run_and_validate, run_compiled


class Suit(IntEnum):
    CLUBS = 5
    DIAMONDS = 6
    HEARTS = 7


def test_simple_genexpr():
    def fn():
        gen = (i for i in range(3))
        for x in gen:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_with_expression():
    def fn():
        gen = (i * 2 for i in range(3))
        for x in gen:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_with_filter():
    def fn():
        gen = (i for i in range(10) if i % 2 == 0)
        for x in gen:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_with_array():
    def fn():
        arr = Array(1, 2, 3, 4, 5)
        gen = (x * 2 for x in arr)
        for x in gen:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_with_tuple():
    def fn():
        tup = (1, 2, 3, 4, 5)
        gen = (x * 2 for x in tup)
        for x in gen:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_nested_loops():
    def fn():
        gen = (i * j for i in range(3) for j in range(2))
        for x in gen:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_nested_loops_with_filter():
    def fn():
        gen = (i * j for i in range(3) for j in range(2) if i + j > 1)
        for x in gen:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_multiple_filters():
    def fn():
        gen = (i for i in range(20) if i % 2 == 0 if i % 3 == 0)
        for x in gen:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_with_builtin_sum():
    def fn():
        gen = (i for i in range(5))
        return sum(gen)

    assert run_and_validate(fn) == 10


def test_genexpr_with_builtin_max():
    def fn():
        gen = (i * 2 for i in range(5))
        return max(gen)

    assert run_and_validate(fn) == 8


def test_genexpr_with_builtin_min():
    def fn():
        gen = (i * 2 + 1 for i in range(5))
        return min(gen)

    assert run_and_validate(fn) == 1


def test_genexpr_with_any():
    def fn():
        gen = (i > 3 for i in range(10))
        return any(gen)

    assert run_and_validate(fn)


def test_genexpr_with_all():
    def fn():
        gen = (i >= 0 for i in range(5))
        return all(gen)

    assert run_and_validate(fn)


def test_genexpr_closure_variable():
    def fn():
        factor = 3
        gen = (i * factor for i in range(5))
        for x in gen:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_multiple_closure_variables():
    def fn():
        base = 2
        multiplier = 3
        gen = (i * multiplier + base for i in range(5))
        for x in gen:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_nested_arrays():
    def fn():
        arr = Array(Array(1, 2), Array(3, 4))
        gen = (x * 2 for sub_arr in arr for x in sub_arr)
        for x in gen:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_nested_tuples():
    def fn():
        tup = ((1, 2), (3, 4))
        gen = (x * 2 for sub_tup in tup for x in sub_tup)
        for x in gen:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_complex_expression():
    def fn():
        gen = (i**2 + 2 * i + 1 for i in range(5))
        for x in gen:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_early_break():
    def fn():
        gen = (i for i in range(100))
        for i, x in enumerate(gen):
            debug_log(x)
            if i >= 2:
                break

    run_and_validate(fn)


def test_genexpr_iterator_resume_after_break():
    def fn():
        gen = (i for i in range(10))

        for i, x in enumerate(gen):
            debug_log(x)
            if i >= 2:
                break

        for i, x in enumerate(gen):
            debug_log(x + 100)
            if i >= 1:
                break

    run_and_validate(fn)


def test_genexpr_empty():
    def fn():
        gen = (i for i in range(0))
        for x in gen:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_filter_excludes_all():
    def fn():
        gen = (i for i in range(5) if i > 10)
        for x in gen:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_changing_closure_sequential():
    def fn():
        x = 0

        gen = (i + x for i in range(3))
        iterator = gen
        for val in iterator:
            debug_log(val)
            break

        x = 10
        for val in iterator:
            debug_log(val)

        return 0

    with pytest.raises(CompilationError, match=r"Binding 'x' has been modified.*"):
        run_compiled(fn)


def test_genexpr_changing_closure_loop():
    def fn():
        x = 0

        gen = (i + x for i in range(3))
        for val in gen:
            debug_log(val)
            x += 1

        return 0

    with pytest.raises(CompilationError, match=r"Binding 'x' has been modified.*"):
        run_compiled(fn)


def test_genexpr_of_gexpr():
    def fn():
        gen = ((i + j for j in range(4)) for i in range(5))
        for sub_gen in gen:
            for x in sub_gen:
                debug_log(x)
                break
            for x in sub_gen:
                debug_log(x + 100)

    run_and_validate(fn)


def test_genexpr_with_next():
    def fn():
        gen = (i * 2 for i in range(5))
        debug_log(next(gen))
        debug_log(next(gen))
        debug_log(next(gen))

    run_and_validate(fn)


def test_genexpr_with_iter():
    def fn():
        gen = (i * 2 for i in range(5))
        iterator = iter(gen)
        for x in iterator:
            debug_log(x)

    run_and_validate(fn)


def test_genexpr_eagerly_evaluates_first_item():
    def fn():
        def first():
            debug_log(1)
            return Array(1, 2, 3)

        def second():
            debug_log(2)
            return Array(4, 5, 6)

        debug_log(3)
        gen = (a + b for a in first() for b in second())
        debug_log(4)
        for x in gen:
            debug_log(x)
        debug_log(5)

    run_and_validate(fn)


def test_genexpr_eagerly_evaluates_first_item_tuples():
    def fn():
        def first():
            debug_log(1)
            return 1, 2, 3

        def second():
            debug_log(2)
            return 4, 5, 6

        debug_log(3)
        gen = (a + b for a in first() for b in second())
        debug_log(4)
        for x in gen:
            debug_log(x)
        debug_log(5)

    run_and_validate(fn)


def test_genexpr_filter_over_tuple():
    def fn():
        return sum(i for i in (1, 2, 3, 4, 5) if i % 2 == 1)

    assert run_and_validate(fn) == 9


def test_genexpr_filter_over_tuple_with_logs():
    def fn():
        def emit(i):
            debug_log(i)
            return i

        gen = (emit(i) for i in (1, 2, 3) if i != 2)
        for x in gen:
            debug_log(x * 10)

    run_and_validate(fn)


def test_genexpr_filter_over_set():
    def fn():
        return sum(i for i in {1, 2, 3, 4} if i % 2 == 0)  # noqa: PLC0208

    assert run_and_validate(fn) == 6


def test_genexpr_filter_over_dict():
    def fn():
        d = {1: 10, 2: 20, 3: 30}
        return sum(k for k in d if k > 1)

    assert run_and_validate(fn) == 5


def test_genexpr_filter_over_enum():
    def fn():
        return sum(int(e) for e in Suit if int(e) > 5)

    assert run_and_validate(fn) == 13


def test_genexpr_filter_over_tuple_with_dynamic_test():
    def fn():
        n = 0
        for _ in range(2):
            n += 1
        return sum(i for i in (1, 2, 3, 4) if i > n)

    assert run_and_validate(fn) == 7


def test_genexpr_filter_on_unrolled_outer_clause():
    def fn():
        return sum(i * 10 + j for i in (1, 2, 3) if i > 1 for j in range(2))

    assert run_and_validate(fn) == 102


def test_genexpr_filter_on_both_unrolled_clauses():
    def fn():
        return sum(i * j for i in (1, 2, 3) if i > 1 for j in (0, 1, 2) if j > 1)

    assert run_and_validate(fn) == 10


def test_genexpr_filter_on_inner_unrolled_clause():
    def fn():
        return sum(i * j for i in range(3) for j in (0, 1, 2) if j > 1)

    assert run_and_validate(fn) == 6


def test_genexpr_filter_multiple_ifs_over_tuple():
    def fn():
        return sum(i for i in (1, 2, 3, 4, 5, 6) if i % 2 == 0 if i > 2)

    assert run_and_validate(fn) == 10


def test_genexpr_filter_over_tuple_all_filtered_out():
    def fn():
        return sum(i for i in (1, 2, 3) if i > 10)

    assert run_and_validate(fn) == 0
