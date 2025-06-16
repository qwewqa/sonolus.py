from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from tests.script.conftest import run_and_validate

ints = st.integers(min_value=-1000, max_value=1000)
floats = st.floats(min_value=-99999, max_value=99999, allow_nan=False, allow_infinity=False)


def test_num_basic():
    def fn():
        return 1

    assert run_and_validate(fn) == 1


@given(n=floats)
def test_num_unary(n):
    def fn():
        plus = +n
        minus = -n
        return Array(plus, minus)

    assert run_and_validate(fn) == Array(+n, -n)


@given(
    a=floats,
    b=floats,
)
def test_num_comparison(a, b):
    def fn():
        lt = a < b
        le = a <= b
        eq = a == b
        ne = a != b
        ge = a >= b
        gt = a > b
        return Array(lt, le, eq, ne, ge, gt)

    assert run_and_validate(fn) == Array(a < b, a <= b, a == b, a != b, a >= b, a > b)


def are_valid_pow_operands(a, b):
    if b > 10:
        # Avoid huge numbers
        return False
    if a < 0 and b % 1 != 0:
        # Avoid complex numbers
        return False
    if a == 0 and b <= 0:
        # Avoid division by zero
        return False
    if abs(a) < 1e-6 and b <= 0:
        # Avoid division by zero or by very small numbers
        return False
    if a < 1 and b < -10:  # noqa: SIM103
        # Avoid division by very small numbers
        return False
    return True


@given(
    a=floats,
    b=floats,
)
def test_num_binary(a, b):
    def fn():
        add = a + b
        sub = a - b
        mul = a * b
        div = a / b if abs(b) > 1e-6 else 0
        mod = a % b if abs(b) > 1e-6 else 0
        power = a**b if are_valid_pow_operands(a, b) else 0
        return Array(add, sub, mul, div, mod, power)

    assert run_and_validate(fn) == Array(
        a + b,
        a - b,
        a * b,
        a / b if abs(b) > 1e-6 else 0,
        a % b if abs(b) > 1e-6 else 0,
        a**b if are_valid_pow_operands(a, b) else 0,
    )


@given(
    a=ints,
    b=ints.filter(lambda x: x != 0),
)
def test_num_floordiv(a, b):
    # floordiv can behave weirdly with float operands due to precision issues, so
    # floor(a / b) may not equal a // b.
    def fn():
        return a // b

    assert run_and_validate(fn) == a // b


@given(
    a=floats,
    b=floats,
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
        if are_valid_pow_operands(a, b):
            power **= b
        floordiv = a
        if b != 0:
            floordiv //= b
        return Array(add, sub, mul, div, mod, power, floordiv)

    assert run_and_validate(fn) == Array(
        a + b,
        a - b,
        a * b,
        a / b if b != 0 else a,
        a % b if b != 0 else a,
        a**b if are_valid_pow_operands(a, b) else a,
        a // b if b != 0 else a,
    )


# What we care about is that compiled behavior matches vanilla Python behavior
# So run_and_validate is more or less enough.


@given(n=ints)
def test_num_while_assignment(n):
    def fn():
        result = 0
        i = 0
        while i < n:
            result += i
            i += 1
        return result

    assert run_and_validate(fn) == sum(range(n))


@given(n=ints)
def test_num_while_else(n):
    def fn():
        result = 0
        i = 0
        while i < n:
            result += i
            i += 1
        result += 100
        return result

    assert run_and_validate(fn) == sum(range(n)) + 100


@given(
    a=ints,
    b=ints,
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

    run_and_validate(fn)


@given(
    a=ints,
    b=ints,
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

    run_and_validate(fn)
