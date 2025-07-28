from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from sonolus.script.debug import debug_log
from sonolus.script.iterator import maybe_next
from sonolus.script.maybe import Nothing, Some
from tests.script.conftest import run_and_validate


def test_simple_some():
    def fn():
        def f():
            return Some(1)

        result = f()
        if result.is_some:
            return result.get()
        else:
            return 0

    assert run_and_validate(fn) == 1


def test_simple_nothing():
    def fn():
        def f():
            return Nothing

        result = f()
        if result.is_some:
            return 1
        else:
            return 0

    assert run_and_validate(fn) == 0


def test_test_multiple_maybe_returns():
    def fn():
        results = VarArray[int, 10].new()

        def f(x):
            if x > 5:
                return Nothing
            if x <= 2:
                return Nothing
            return Some(x * 2)

        for i in range(10):
            result = f(i)
            if result.is_some:
                results.append(result.get())

        return results

    assert list(run_and_validate(fn)) == [6, 8, 10]


def test_maybe_in_generator():
    def fn():
        def generator(n):
            for i in range(n):
                if i % 2 == 0:
                    yield Some(i)
                else:
                    yield Nothing

        results = VarArray[int, 10].new()
        for result in generator(10):
            if result.is_some:
                results.append(result.get())

        return results

    assert list(run_and_validate(fn)) == [0, 2, 4, 6, 8]


def test_maybe_map_with_some():
    def fn():
        maybe_val = Some(5)
        mapped = maybe_val.map(lambda x: x * 2)
        if mapped.is_some:
            return mapped.get()
        else:
            return 0

    assert run_and_validate(fn) == 10


def test_maybe_map_with_nothing():
    def fn():
        maybe_val = Nothing
        mapped = maybe_val.map(lambda x: x * 2)
        if mapped.is_some:
            return 1
        else:
            return 0

    assert run_and_validate(fn) == 0


def test_maybe_flat_map_with_some():
    def fn():
        maybe_val = Some(5)
        flat_mapped = maybe_val.flat_map(lambda x: Some(x * 3) if x > 0 else Nothing)
        if flat_mapped.is_some:
            return flat_mapped.get()
        else:
            return 0

    assert run_and_validate(fn) == 15


def test_maybe_flat_map_with_nothing():
    def fn():
        maybe_val = Nothing
        flat_mapped = maybe_val.flat_map(lambda x: Some(x * 3) if x > 0 else Nothing)
        if flat_mapped.is_some:
            return 1
        else:
            return 0

    assert run_and_validate(fn) == 0


def test_maybe_flat_map_returns_nothing():
    def fn():
        maybe_val = Some(-5)
        flat_mapped = maybe_val.flat_map(lambda x: Nothing if x < 0 else Some(x))
        if flat_mapped.is_some:
            return 1
        else:
            return 0

    assert run_and_validate(fn) == 0


def test_maybe_or_default_with_some():
    def fn():
        maybe_val = Some(42)
        return maybe_val.or_default(10)

    assert run_and_validate(fn) == 42


def test_maybe_or_default_with_nothing():
    def fn():
        maybe_val = Nothing
        return maybe_val.or_default(10)

    assert run_and_validate(fn) == 10


def test_maybe_or_else_with_some():
    def fn():
        maybe_val = Some(42)
        return maybe_val.or_else(lambda: 10)

    assert run_and_validate(fn) == 42


def test_maybe_or_else_with_nothing():
    def fn():
        maybe_val = Nothing
        return maybe_val.or_else(lambda: 15)

    assert run_and_validate(fn) == 15


def test_maybe_or_else_dynamic():
    def fn():
        iterator = iter(Array(1))
        debug_log(maybe_next(iterator).or_else(lambda: 4))
        debug_log(maybe_next(iterator).or_else(lambda: 5))
        debug_log(maybe_next(iterator).or_else(lambda: 6))

    run_and_validate(fn)


def test_maybe_tuple_with_some():
    def fn():
        maybe_val = Some(42)
        is_present, value = maybe_val.tuple
        if is_present:
            return value
        else:
            return 0

    assert run_and_validate(fn) == 42


def test_maybe_tuple_with_nothing():
    def fn():
        maybe_val = Nothing
        is_present, value = maybe_val.tuple
        if is_present:
            return value
        else:
            return 123

    assert run_and_validate(fn) == 123


def test_maybe_is_nothing_property():
    def fn():
        some_val = Some(42)
        nothing_val = Nothing

        result = 0
        if some_val.is_nothing:
            result += 1
        if nothing_val.is_nothing:
            result += 10

        return result

    assert run_and_validate(fn) == 10


def test_maybe_next_with_empty_iterable():
    def fn():
        empty_list = Array[int, 0]()
        result = maybe_next(iter(empty_list))
        if result.is_some:
            return 1
        else:
            return 0

    assert run_and_validate(fn) == 0


def test_maybe_next_with_non_empty_iterable():
    def fn():
        items = Array(1, 2, 3)
        result = maybe_next(iter(items))
        if result.is_some:
            return result.get()
        else:
            return 0

    assert run_and_validate(fn) == 1
