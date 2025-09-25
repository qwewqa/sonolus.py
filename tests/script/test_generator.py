import pytest

from sonolus.script.array import Array
from sonolus.script.containers import Box
from sonolus.script.debug import debug_log
from sonolus.script.internal.error import CompilationError
from tests.script.conftest import run_and_validate, run_compiled


def test_simple_generator():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        for i in gen():
            debug_log(i)

    run_and_validate(fn)


def test_generator_interspersed():
    def fn():
        def gen():
            debug_log(1)
            yield 1
            debug_log(2)
            yield 2
            debug_log(3)
            yield 3

        for i in gen():
            debug_log(i)

    run_and_validate(fn)


def test_generator_laziness():
    def fn():
        def gen():
            debug_log(1)
            yield 1
            debug_log(2)
            yield 2
            debug_log(3)
            yield 3

        iterator = gen()
        debug_log(0)
        for i in iterator:
            debug_log(i)

    run_and_validate(fn)


def test_generator_over_array():
    def fn():
        arr = Array(1, 2, 3, 4, 5)

        def gen():
            for i in arr:
                yield i * 2

        for i in gen():
            debug_log(i)

    run_and_validate(fn)


def test_generator_over_tuple():
    def fn():
        tup = (1, 2, 3, 4, 5)

        def gen():
            for i in tup:
                yield i * 2

        for i in gen():
            debug_log(i)

    run_and_validate(fn)


def test_generator_over_nested_array():
    def fn():
        arr = Array(Array(1, 2), Array(3, 4))

        def gen():
            for sub_arr in arr:
                for i in sub_arr:
                    yield i * 2

        for i in gen():
            debug_log(i)

    run_and_validate(fn)


def test_generator_over_nested_tuple():
    def fn():
        tup = ((1, 2), (3, 4))

        def gen():
            for sub_tup in tup:
                for i in sub_tup:
                    yield i * 2

        for i in gen():
            debug_log(i)

    run_and_validate(fn)


def test_generator_over_nested_array_breaks():
    def fn():
        arr = Array(Array(1, 2), Array(3, 4))

        def gen():
            for sub_arr in arr:
                for i in sub_arr:
                    yield i * 2
                yield 123

        iterator = gen()
        for i in iterator:
            debug_log(i)
            break
        for i in iterator:
            debug_log(i * 2)

    run_and_validate(fn)


def test_generator_over_nested_tuple_breaks():
    def fn():
        tup = ((1, 2), (3, 4))

        def gen():
            for sub_tup in tup:
                for i in sub_tup:
                    yield i * 2
                yield 123

        iterator = gen()
        for i in iterator:
            debug_log(i)
            break
        for i in iterator:
            debug_log(i * 2)

    run_and_validate(fn)


def test_generator_with_skipping_loop():
    def fn():
        def gen():
            for i in range(10):
                if i % 2 == 0:  # Skip even numbers
                    continue
                yield i

        for i in gen():
            debug_log(i)

    run_and_validate(fn)


def test_generator_with_any_true():
    def fn():
        def gen():
            debug_log(1)
            yield 0
            debug_log(2)
            yield 1
            debug_log(3)
            yield 2

        return any(gen())

    assert run_and_validate(fn)


def test_generator_with_all_true():
    def fn():
        def gen():
            debug_log(1)
            yield 1
            debug_log(2)
            yield 2
            debug_log(3)
            yield 3

        return all(gen())

    assert run_and_validate(fn)


def test_generator_with_any_false():
    def fn():
        def gen():
            debug_log(1)
            yield 0
            debug_log(2)
            yield 0
            debug_log(3)
            yield 0

        return any(gen())

    assert not run_and_validate(fn)


def test_generator_with_all_false():
    def fn():
        def gen():
            debug_log(1)
            yield 0
            debug_log(2)
            yield 0
            debug_log(3)
            yield 0

        return all(gen())

    assert not run_and_validate(fn)


def test_generator_with_sum():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        return sum(gen())

    assert run_and_validate(fn) == 6


def test_generator_with_max():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        return max(gen())

    assert run_and_validate(fn) == 3


def test_generator_with_min():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        return min(gen())

    assert run_and_validate(fn) == 1


def test_generator_with_max_default():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        return max(gen(), default=0)

    assert run_and_validate(fn) == 3


def test_generator_with_min_default():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        return min(gen(), default=0)

    assert run_and_validate(fn) == 1


def test_empty_generator_with_max_default():
    def fn():
        def gen():
            return
            yield 1

        return max(gen(), default=0)

    assert run_and_validate(fn) == 0


def test_empty_generator_with_min_default():
    def fn():
        def gen():
            return
            yield 1

        return min(gen(), default=0)

    assert run_and_validate(fn) == 0


def test_generator_with_max_key():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        return max(gen(), key=lambda x: -x)

    assert run_and_validate(fn) == 1


def test_generator_with_min_key():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        return min(gen(), key=lambda x: -x)

    assert run_and_validate(fn) == 3


def test_parallel_generator():
    def fn():
        def inner_gen():
            yield 1
            yield 2

        def outer_gen():
            yield from inner_gen()
            yield 3

        for i in outer_gen():
            debug_log(i)

    run_and_validate(fn)


def test_nested_generator():
    def fn():
        def outer_gen():
            def inner_gen():
                yield 1
                yield 2

            yield from inner_gen()
            yield 3

        for i in outer_gen():
            debug_log(i)

    run_and_validate(fn)


def test_generator_changing_closure_loop():
    def fn():
        x = 0

        def gen():
            yield x
            yield x

        for i in gen():
            debug_log(i)
            x += 1

        return 0

    with pytest.raises(CompilationError, match=r"Binding 'x' has been modified.*"):
        run_compiled(fn)


def test_generator_changing_closure_sequential():
    def fn():
        x = 0

        def gen():
            yield x
            yield x

        iterator = gen()
        for i in iterator:
            debug_log(i)
            break

        x = 2
        for i in iterator:
            debug_log(i)

        return 0

    with pytest.raises(CompilationError, match=r"Binding 'x' has been modified.*"):
        run_compiled(fn)


def test_generator_with_return_in_middle():
    def fn():
        def gen():
            yield 1
            return
            yield 3

        for i in gen():
            debug_log(i)

    run_and_validate(fn)


def test_generator_with_return_in_loop():
    def fn():
        def gen():
            for i in range(5):
                if i == 3:
                    return
                yield i

        for i in gen():
            debug_log(i)

    run_and_validate(fn)


def test_infinite_generator():
    def fn():
        def gen():
            while True:
                yield 1

        for i, v in enumerate(gen()):
            debug_log(v)
            if i >= 5:
                break

    run_and_validate(fn)


def test_generator_with_single_parameter():
    def fn():
        def gen(x):
            yield x
            yield x * 2
            yield x * 3

        for i in gen(5):
            debug_log(i)

    run_and_validate(fn)


def test_generator_with_multiple_parameters():
    def fn():
        def gen(x, y):
            yield x
            yield y
            yield x + y

        for i in gen(10, 20):
            debug_log(i)

    run_and_validate(fn)


def test_generator_with_parameter_and_loop():
    def fn():
        def gen(multiplier):
            for i in range(3):
                yield i * multiplier

        for i in gen(4):
            debug_log(i)

    run_and_validate(fn)


def test_generator_with_parameter_over_array():
    def fn():
        arr = Array(1, 2, 3)

        def gen(factor):
            for i in arr:
                yield i * factor

        for i in gen(3):
            debug_log(i)

    run_and_validate(fn)


def test_generator_with_default_parameter():
    def fn():
        def gen(x=7):
            yield x
            yield x * 2

        for i in gen():
            debug_log(i)

    run_and_validate(fn)


def test_generator_with_mixed_parameters():
    def fn():
        def gen(x, y=10):
            yield x
            yield y
            yield x * y

        for i in gen(5):
            debug_log(i)

    run_and_validate(fn)


def test_generator_with_parameter_in_nested_call():
    def fn():
        def inner_gen(value):
            yield value
            yield value + 1

        def outer_gen(base):
            yield from inner_gen(base)
            yield from inner_gen(base * 2)

        for i in outer_gen(3):
            debug_log(i)

    run_and_validate(fn)


def test_generator_yielding_record():
    def fn():
        def gen():
            for i in range(10):
                yield Box(i)

        for record in gen():
            debug_log(record.value)

    run_and_validate(fn)


def test_generator_yielding_record_mutation():
    def fn():
        box = Box(1)

        def gen():
            yield box
            yield box
            yield box

        for record in gen():
            debug_log(record.value)

        for record in gen():
            debug_log(record.value)
            box.value = 2

    run_and_validate(fn)


def test_generator_yielding_array_record_element_with_mutation():
    def fn():
        arr = Array(Box(1), Box(2), Box(3))

        def gen():
            yield from arr

        for record in gen():
            debug_log(record.value)
            record.value = 10

        for record in gen():
            arr[0].value = 20
            debug_log(record.value)

        for element in gen():
            debug_log(element.value)

    run_and_validate(fn)


def test_nested_iteration_of_same_generator():
    def fn():
        arr = Array(Box(0), Box(1), Box(2))

        def gen():
            for i in range(3):
                yield arr[i]

        for record in gen():
            for record_2 in gen():
                record.value = 1
                debug_log(record.value + record_2.value)

    run_and_validate(fn)


def test_generator_with_next():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        iterator = gen()
        debug_log(next(iterator))
        debug_log(next(iterator))
        debug_log(next(iterator))

    run_and_validate(fn)


def test_generator_with_iter():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        iterator = iter(gen())
        for i in iterator:
            debug_log(i)

    run_and_validate(fn)


def test_comptime_empty_generator():
    def fn():
        def gen():
            return
            yield lambda: 1

        for i in gen():
            return i()
        return 2

    assert run_and_validate(fn) == 2
