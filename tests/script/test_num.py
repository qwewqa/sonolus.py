from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from tests.script.conftest import validate_dual_run


def test_num_basic():
    def fn():
        return 1

    assert validate_dual_run(fn) == 1


@given(n=st.integers(min_value=-100, max_value=100))
def test_num_unary(n):
    def fn():
        plus = +n
        minus = -n
        return Array.of(plus, minus)

    assert validate_dual_run(fn) == Array.of(+n, -n)


@given(
    a=st.integers(min_value=-100, max_value=100),
    b=st.integers(min_value=-100, max_value=100),
)
def test_num_comparison(a, b):
    def fn():
        lt = a < b
        le = a <= b
        eq = a == b
        ne = a != b
        ge = a >= b
        gt = a > b
        return Array.of(lt, le, eq, ne, ge, gt)

    assert validate_dual_run(fn) == Array.of(a < b, a <= b, a == b, a != b, a >= b, a > b)


@given(
    a=st.integers(min_value=-100, max_value=100),
    b=st.integers(min_value=-100, max_value=100),
)
def test_num_binary(a, b):
    def fn():
        add = a + b
        sub = a - b
        mul = a * b
        div = a / b if b != 0 else 0
        mod = a % b if b != 0 else 0
        power = a**b if (b < 10 and (a > 0 or b > 0)) else 0
        floordiv = a // b if b != 0 else 0
        return Array.of(add, sub, mul, div, mod, power, floordiv)

    assert validate_dual_run(fn) == Array.of(
        a + b,
        a - b,
        a * b,
        a / b if b != 0 else 0,
        a % b if b != 0 else 0,
        a**b if (b < 10 and (a > 0 or b > 0)) else 0,
        a // b if b != 0 else 0,
    )


@given(
    a=st.integers(min_value=-100, max_value=100),
    b=st.integers(min_value=-100, max_value=100),
)
def test_num_augmented(a, b):
    def fn():
        add = a
        add += b
        sub = a
        sub -= b
        mul = a
        mul *= b
        div = a
        if b != 0:
            div /= b
        mod = a
        if b != 0:
            mod %= b
        power = a
        if b < 10 and (a > 0 or b > 0):
            power **= b
        floordiv = a
        if b != 0:
            floordiv //= b
        return Array.of(add, sub, mul, div, mod, power, floordiv)

    assert validate_dual_run(fn) == Array.of(
        a + b,
        a - b,
        a * b,
        a / b if b != 0 else a,
        a % b if b != 0 else a,
        a**b if (b < 10 and (a > 0 or b > 0)) else a,
        a // b if b != 0 else a,
    )


# What we care about is that compiled behavior matches vanilla Python behavior
# So validate_dual_run is more or less enough.


@given(n=st.integers(min_value=-100, max_value=100))
def test_num_while_assignment(n):
    def fn():
        result = 0
        i = 0
        while i < n:
            result += i
            i += 1
        return result

    assert validate_dual_run(fn) == sum(range(n))


@given(n=st.integers(min_value=-100, max_value=100))
def test_num_while_else(n):
    def fn():
        result = 0
        i = 0
        while i < n:
            result += i
            i += 1
        result += 100
        return result

    assert validate_dual_run(fn) == sum(range(n)) + 100


@given(
    a=st.integers(min_value=-100, max_value=100),
    b=st.integers(min_value=-100, max_value=100),
)
def test_num_while_break(a, b):
    def fn():
        result = 0
        i = 0
        while i < a:
            result += i
            i += 1
            if i == b:
                break
        else:
            result = -1
        return result

    validate_dual_run(fn)


@given(
    a=st.integers(min_value=-100, max_value=100),
    b=st.integers(min_value=-100, max_value=100),
)
def test_num_while_continue(a, b):
    def fn():
        result = 0
        i = 0
        while i < a:
            i += 1
            if i == b:
                result += 100
                continue
            result += i - 1
        return result

    validate_dual_run(fn)
