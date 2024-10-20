from script.conftest import dual_run

from sonolus.script.range import Range


def test_basic_range_iteration():
    def fn():
        total = 0
        for i in Range(5):
            total += i
        return total

    result = dual_run(fn)
    assert result == 10
