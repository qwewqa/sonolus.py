from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from tests.script.conftest import run_and_validate


def test_dict_get_existing():
    def fn():
        d = {"a": 1, "b": 2}
        return Array(d["a"], d["b"])

    assert run_and_validate(fn) == Array(1, 2)


def test_dict_in_operator():
    def fn():
        d = {"a": 1, "b": 2}
        return Array(
            "a" in d,  # True
            "b" in d,  # True
            "c" in d,  # False
            "d" in d,  # False
            "a" not in d,  # False
            "b" not in d,  # False
            "c" not in d,  # True
            "d" not in d,  # True
        )

    assert run_and_validate(fn) == Array(True, True, False, False, False, False, True, True)


@given(
    v1=st.integers(min_value=-10, max_value=10),
    v2=st.integers(min_value=-10, max_value=10),
)
def test_dict_union(v1, v2):
    def fn():
        d1 = {"a": v1}
        d2 = {"b": v2}
        d3 = d1 | d2
        return Array(d3["a"], d3["b"])

    assert run_and_validate(fn) == Array(v1, v2)
