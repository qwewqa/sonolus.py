from enum import IntEnum

from sonolus.script.containers import VarArray
from tests.script.conftest import run_and_validate


class Color(IntEnum):
    RED = 1
    GREEN = 2
    BLUE = 3


def test_len_int_enum():
    def fn():
        return len(Color)

    assert run_and_validate(fn) == len(Color)


def test_iterate_int_enum():
    n = len(Color)

    def fn():
        results = VarArray[int, n].new()
        for c in Color:
            results.append(c)
        return results

    assert list(run_and_validate(fn)) == [c.value for c in Color]
