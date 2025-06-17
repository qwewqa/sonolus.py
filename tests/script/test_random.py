# ruff: noqa: PT019
import random
from collections import defaultdict
from collections.abc import Callable

from hypothesis import given
from hypothesis import strategies as st

import sonolus.script.internal.random as srandom
from sonolus.script.array import Array
from sonolus.script.debug import assert_true
from sonolus.script.values import copy
from tests.script.conftest import run_compiled

# Strategies
ints = st.integers(min_value=-999, max_value=999)
floats = st.floats(min_value=-999, max_value=999, allow_infinity=False, allow_nan=False)
lists = st.lists(ints, min_size=1, max_size=20)


@given(st.random_module())
def test_randrange_basic(_r):
    def fn():
        value = random.randrange(10)
        assert_true(0 <= value < 10)
        return value

    result = run_compiled(fn)
    assert 0 <= result < 10


@given(st.random_module(), st.integers(min_value=-100, max_value=100), st.integers(min_value=1, max_value=100))
def test_randrange_with_start_stop(_r, start, width):
    stop = start + width

    def fn():
        value = random.randrange(start, stop)
        assert_true(start <= value < stop)
        return value

    result = run_compiled(fn)
    assert start <= result < stop


@given(
    st.random_module(),
    st.integers(min_value=-100, max_value=100),
    st.integers(min_value=1, max_value=100),
    st.integers(min_value=1, max_value=10),
)
def test_randrange_with_step(_r, start, width, step):
    stop = start + width

    def fn():
        value = random.randrange(start, stop, step)
        assert_true(start <= value < stop)
        assert_true((value - start) % step == 0)
        return value

    result = run_compiled(fn)
    assert start <= result < stop
    assert (result - start) % step == 0


@given(st.random_module(), st.integers(min_value=-100, max_value=100), st.integers(min_value=0, max_value=100))
def test_randint(_r, a, width):
    b = a + width

    def fn():
        value = random.randint(a, b)
        assert_true(a <= value <= b)
        return value

    result = run_compiled(fn)
    assert a <= result <= b


@given(st.random_module(), lists)
def test_choice(_r, values_list):
    values = Array(*values_list)

    def fn():
        value = random.choice(values)
        return value

    result = run_compiled(fn)
    assert result in values_list


@given(st.random_module(), lists)
def test_shuffle(_r, values_list):
    values = Array(*values_list)

    def fn():
        a = copy(values)
        random.shuffle(a)
        a.sort()
        b = copy(values)
        b.sort()
        return a == b

    assert run_compiled(fn)


@given(st.random_module())
def test_random(_r):
    def fn():
        value = random.random()
        assert_true(0.0 <= value < 1.0)
        return value

    result = run_compiled(fn)
    assert 0.0 <= result < 1.0


@given(st.random_module(), st.floats(min_value=-100.0, max_value=100.0), st.floats(min_value=0.0, max_value=100.0))
def test_uniform(_r, a, width):
    b = a + width

    def fn():
        value = random.uniform(a, b)
        assert_true(a <= value <= b)
        return value

    result = run_compiled(fn)
    assert a <= result <= b


MAX_ITERATIONS = 10000
MAX_RANGE_SIZE = 10


def collect_until_complete[T](
    generator: Callable[[], T], expected_values: set[T], max_iterations: int = MAX_ITERATIONS
) -> tuple[dict[T, int], int]:
    """Generate values until all expected values are generated or max iterations are reached."""
    counts: dict[T, int] = defaultdict(int)
    missing = expected_values.copy()
    iterations = 0

    while missing and iterations < max_iterations:
        value = generator()
        counts[value] += 1
        missing.discard(value)
        iterations += 1

    return counts, len(missing)


@given(st.random_module(), st.integers(min_value=1, max_value=MAX_RANGE_SIZE))
def test_impl_randrange_simple(_random_module, stop):
    expected = set(range(stop))
    counts, missing = collect_until_complete(lambda: srandom._randrange(stop), expected)

    assert missing == 0, f"Failed to generate values: {expected - set(counts.keys())}"


@given(
    st.random_module(),
    st.integers(min_value=-MAX_RANGE_SIZE, max_value=MAX_RANGE_SIZE),
    st.integers(min_value=1, max_value=MAX_RANGE_SIZE),
)
def test_impl_randrange_start_stop(_random_module, start, width):
    stop = start + width
    expected = set(range(start, stop))
    counts, missing = collect_until_complete(lambda: srandom._randrange(start, stop), expected)

    assert missing == 0, f"Failed to generate values: {expected - set(counts)}"
    assert all(start <= x < stop for x in counts)


@given(
    st.random_module(),
    st.integers(min_value=-MAX_RANGE_SIZE, max_value=MAX_RANGE_SIZE),
    st.integers(min_value=1, max_value=MAX_RANGE_SIZE),
    st.integers(min_value=1, max_value=5),
)
def test_impl_randrange_with_step(_random_module, start, width, step):
    stop = start + width
    expected = set(range(start, stop, step))
    if not expected:  # Empty range
        return

    counts, missing = collect_until_complete(lambda: srandom._randrange(start, stop, step), expected)

    assert missing == 0, f"Failed to generate values: {expected - set(counts)}"
    assert all((x - start) % step == 0 for x in counts)
    assert all(start <= x < stop for x in counts)


@given(
    st.random_module(),
    st.integers(min_value=-MAX_RANGE_SIZE, max_value=MAX_RANGE_SIZE),
    st.integers(min_value=0, max_value=MAX_RANGE_SIZE),
)
def test_impl_randint(_random_module, a, width):
    b = a + width
    expected = set(range(a, b + 1))
    counts, missing = collect_until_complete(lambda: srandom._randint(a, b), expected)

    assert missing == 0, f"Failed to generate values: {expected - set(counts.keys())}"
    assert all(a <= x <= b for x in counts)


@given(
    st.random_module(),
    st.lists(
        st.integers(min_value=-MAX_RANGE_SIZE, max_value=MAX_RANGE_SIZE),
        min_size=1,
        max_size=MAX_RANGE_SIZE,
        unique=True,
    ),
)
def test_impl_choice(_random_module, options):
    expected = set(options)
    counts, missing = collect_until_complete(lambda: srandom._choice(options), expected)

    assert missing == 0, f"Failed to generate values: {expected - set(counts.keys())}"


@given(
    st.random_module(),
    st.lists(st.integers(min_value=-MAX_RANGE_SIZE, max_value=MAX_RANGE_SIZE), min_size=1, max_size=5, unique=True),
)
def test_impl_shuffle(_random_module, original):
    # For small lists, we can test all permutations
    def get_permutations(lst: list[int]) -> set[tuple]:
        if len(lst) <= 1:
            return {tuple(lst)}
        result = set()
        for i in range(len(lst)):
            result.update((lst[i], *perm) for perm in get_permutations(lst[:i] + lst[i + 1 :]))
        return result

    expected_permutations = get_permutations(original)

    def generate_shuffle():
        test_impl_list = original.copy()
        srandom._shuffle(test_impl_list)
        return tuple(test_impl_list)

    counts, missing = collect_until_complete(generate_shuffle, expected_permutations)

    assert missing == 0, f"Failed to generate permutations: {expected_permutations - set(counts.keys())}"


@given(
    st.random_module(),
)
def test_impl_random_distribution(_random_module):
    # Test if random() generates values across all buckets
    num_buckets = 10
    bucket_size = 1.0 / num_buckets
    expected = set(range(num_buckets))

    def get_bucket():
        value = srandom._random()
        assert 0 <= value < 1, f"Value {value} outside [0,1)"
        return int(value / bucket_size)

    counts, missing = collect_until_complete(get_bucket, expected)

    assert missing == 0, f"Failed to generate values in buckets: {expected - set(counts.keys())}"


@given(
    st.random_module(),
    st.floats(min_value=-MAX_RANGE_SIZE, max_value=MAX_RANGE_SIZE),
    st.floats(min_value=0.1, max_value=MAX_RANGE_SIZE),
)
def test_impl_uniform_distribution(_random_module, a, width):
    num_buckets = 10
    b = a + width
    bucket_size = width / num_buckets
    expected = set(range(num_buckets))

    def get_bucket():
        value = srandom._uniform(a, b)
        assert a <= value <= b, f"Value {value} outside [{a},{b}]"
        bucket = int((value - a) / bucket_size)
        return min(bucket, num_buckets - 1)  # Clamp to last bucket if value is b

    counts, missing = collect_until_complete(get_bucket, expected)

    assert missing == 0, f"Failed to generate values in buckets: {expected - set(counts.keys())}"
