from sonolus.script.containers import VarArray
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
