"""Tests for Python builtin functions (min, max, ...) as implemented for sonolus scripts.

PYTEST_DONT_REWRITE
"""

from enum import IntEnum

import pytest

from sonolus.script.array import Array
from sonolus.script.array_like import ArrayLike
from sonolus.script.containers import VarArray
from sonolus.script.debug import debug_log
from sonolus.script.internal.builtin_impls import _max, _min  # noqa: PLC2701
from sonolus.script.internal.context import RuntimeChecks
from sonolus.script.internal.error import CompilationError
from sonolus.script.iterator import SonolusIterator
from sonolus.script.maybe import Nothing, Some
from sonolus.script.num import Num
from sonolus.script.record import Record
from sonolus.script.vec import Vec2
from tests.script.conftest import compile_fn, run_and_validate, run_compiled
from tests.script.test_flow import black_box_value


def test_max_comptime_honors_key():
    assert _max(Num(1.0), Num(3.0), key=lambda v: -v) == 1


def test_min_comptime_honors_key():
    assert _min(Num(1.0), Num(3.0), key=lambda v: -v) == 3


class _Pt(Record):
    x: int
    y: int


class _InvalidIterable(Record):
    def __iter__(self):
        return None


class _CountingIterator(Record, SonolusIterator):
    value: int
    done: int

    def __iter__(self):
        debug_log(self.value)
        return self

    def next(self):
        if self.done:
            return Nothing
        self.done = 1
        return Some(self.value)


def _plain_fn(x):
    return x + 1


def test_callable_on_user_function():
    def fn():
        return callable(_plain_fn)

    assert run_and_validate(fn) is True


def test_callable_on_builtin():
    def fn():
        return callable(len)

    assert run_and_validate(fn) is True


def test_callable_on_lambda():
    def fn():
        return callable(lambda x: x)

    assert run_and_validate(fn) is True


def test_callable_on_method():
    def fn():
        return callable(Array(1, 2, 3).index)

    assert run_and_validate(fn) is True


def test_callable_on_classmethod():
    def fn():
        return callable(Array[int, 3].element_type)

    assert run_and_validate(fn) is True


def test_callable_on_type():
    def fn():
        return callable(Vec2)

    assert run_and_validate(fn) is True


def test_callable_on_int_type():
    def fn():
        return callable(int)

    assert run_and_validate(fn) is True


def test_callable_on_type_builtin():
    def fn():
        return callable(type)

    assert run_and_validate(fn) is True


def test_callable_on_num():
    def fn():
        return callable(5)

    assert run_and_validate(fn) is False


def test_callable_on_runtime_num():
    def fn():
        v = 1
        v += 1
        return callable(v)

    assert run_and_validate(fn) is False


def test_callable_on_record_instance():
    def fn():
        return callable(_Pt(1, 2))

    assert run_and_validate(fn) is False


def test_callable_on_array():
    def fn():
        return callable(Array(1, 2, 3))

    assert run_and_validate(fn) is False


def test_callable_on_tuple():
    def fn():
        return callable((1, 2, 3))

    assert run_and_validate(fn) is False


def test_callable_on_string():
    def fn():
        return callable("abc")

    assert run_and_validate(fn) is False


def test_iter_on_tuple_raises_clear_error():
    def fn():
        return next(iter((1, 2, 3)))

    with pytest.raises(CompilationError, match=r"tuple .* compile-time construct .* no iterator"):
        compile_fn(fn)


def test_iter_on_tuple_error_mentions_alternative():
    def fn():
        return next(iter((1, 2, 3)))

    with pytest.raises(CompilationError, match="iterate over it directly"):
        compile_fn(fn)


def test_iter_on_array_still_works():
    def fn():
        return next(iter(Array(4, 5, 6)))

    assert run_and_validate(fn) == 4


@pytest.mark.parametrize(
    "make_fn",
    [
        lambda: iter(_InvalidIterable()),
        lambda: map(lambda x: x, _InvalidIterable()),  # noqa: C417
        lambda: filter(None, _InvalidIterable()),
    ],
    ids=["iter", "map", "filter"],
)
def test_iterator_builtins_reject_invalid_iter_result(make_fn):
    def fn():
        make_fn()
        return 0

    with pytest.raises(TypeError, match=r"iter\(\) returned non-iterator of type 'NoneType'"):
        run_and_validate(fn)


def test_map_calls_iter_once_per_input():
    def fn():
        return sum(map(lambda a, b: a + b, _CountingIterator(1, 0), _CountingIterator(2, 0)))

    assert run_and_validate(fn) == 3


class _BoolByField(Record):
    v: int

    def __bool__(self):
        return self.v != 0


class _LenRecord(Record):
    n: int

    def __len__(self) -> int:
        return self.n


class _InvalidLenArray(Record, ArrayLike[int]):
    n: int

    def __len__(self) -> int:
        return self.n

    def __getitem__(self, index: int) -> int:
        return index

    def __setitem__(self, index: int, value: int):
        pass


class _NonnumericLenRecord(Record):
    def __len__(self):
        return None  # noqa: PLE0303 - intentionally violates the protocol


def test_bool_no_arg():
    def fn():
        return bool()  # noqa: UP018

    assert run_and_validate(fn) is False


def test_bool_comptime_truthy_num():
    def fn():
        return bool(5)

    assert run_and_validate(fn) is True


def test_bool_comptime_falsy_num():
    def fn():
        return bool(0)

    assert run_and_validate(fn) is False


def test_bool_runtime_truthy_num():
    def fn():
        x = 0
        for i in range(3):
            x += i
        return bool(x)

    assert run_and_validate(fn) is True


def test_bool_runtime_falsy_num():
    def fn():
        x = 0
        for _ in range(3):
            x += 0
        return bool(x)

    assert run_and_validate(fn) is False


def test_bool_runtime_num_normalizes_to_one():
    # bool() must return 0/1, not the original value: 3 + 3 == 6 would show up here if it did not.
    def fn():
        x = 0
        for i in range(3):
            x += i
        return bool(x) + bool(x)

    assert run_and_validate(fn) == 2


def test_bool_runtime_negative_num():
    def fn():
        x = 0
        for _ in range(3):
            x -= 1
        return bool(x)

    assert run_and_validate(fn) is True


def test_bool_record_with_runtime_bool():
    def fn():
        x = 0
        for i in range(3):
            x += i
        return bool(_BoolByField(x))

    assert run_and_validate(fn) is True


def test_bool_record_with_runtime_bool_falsy():
    def fn():
        x = 0
        for _ in range(3):
            x += 0
        return bool(_BoolByField(x))

    assert run_and_validate(fn) is False


def test_bool_record_with_comptime_bool_returning_num():
    # __bool__ returns a Num even for a compile-time receiver, which the host-side truth test rejected.
    def fn():
        return bool(_BoolByField(0))

    assert run_and_validate(fn) is False


def test_bool_record_with_runtime_len():
    def fn():
        n = 0
        for _ in range(3):
            n += 0
        return bool(_LenRecord(n))

    assert run_and_validate(fn) is False


@pytest.mark.parametrize("operation", ["len", "bool"])
@pytest.mark.parametrize(("invalid_length", "expected_error"), [(-1, ValueError), (0.5, TypeError)])
def test_len_protocol_rejects_invalid_runtime_result(operation, invalid_length, expected_error):
    def fn():
        n = 0
        for _ in range(1):
            n += invalid_length
        value = _LenRecord(n)
        return len(value) if operation == "len" else bool(value)

    with pytest.raises(expected_error):
        run_and_validate(fn)


@pytest.mark.parametrize("operation", ["len", "bool"])
def test_len_protocol_rejects_nonnumeric_result(operation):
    def fn():
        value = _NonnumericLenRecord()
        return len(value) if operation == "len" else bool(value)

    # The subset rejects this statically, so there is no runtime leg to compare with Python's TypeError.
    with pytest.raises(CompilationError, match="Invalid type for __len__: NoneType"):
        compile_fn(fn)


@pytest.mark.parametrize("invalid_length", [-1, 0.5])
def test_array_extremum_rejects_invalid_runtime_len(invalid_length):
    # This pins the compiled ArrayLike protocol: Python's sequence iterator does not consult __len__ for max().
    def fn():
        n = 0
        for _ in range(1):
            n += invalid_length
        return max(_InvalidLenArray(n), default=10) + 100

    assert run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE) == 0


def test_bool_record_without_bool_or_len():
    def fn():
        return bool(_Pt(0, 0))

    assert run_and_validate(fn) is True


def test_bool_on_runtime_record_without_bool_or_len():
    def fn():
        p = _Pt(0, 0)
        for i in range(3):
            p.x += i
        return bool(p)

    assert run_and_validate(fn) is True


def test_bool_on_array():
    def fn():
        return bool(Array(1, 2, 3))

    assert run_and_validate(fn) is True


def test_bool_on_tuple():
    def fn():
        return bool((1, 2, 3))

    assert run_and_validate(fn) is True


def test_bool_on_empty_tuple():
    def fn():
        return bool(())

    assert run_and_validate(fn) is False


def test_bool_on_string():
    def fn():
        return bool("abc")

    assert run_and_validate(fn) is True


def test_bool_on_empty_string():
    def fn():
        return bool("")

    assert run_and_validate(fn) is False


def test_bool_on_none():
    def fn():
        return bool(None)

    assert run_and_validate(fn) is False


def test_bool_on_dict():
    def fn():
        return bool({1: 2})

    assert run_and_validate(fn) is True


def test_bool_on_set():
    def fn():
        return bool({1, 2})

    assert run_and_validate(fn) is True


def test_bool_of_runtime_num_usable_as_condition():
    def fn():
        x = 0
        for i in range(3):
            x += i
        if bool(x):
            return 10
        return 20

    assert run_and_validate(fn) == 10


def test_sum_numeric_no_start():
    def fn():
        return sum((1, 2, 3))

    assert run_and_validate(fn) == 6


def test_sum_numeric_with_start():
    def fn():
        return sum((1, 2, 3), 10)

    assert run_and_validate(fn) == 16


def test_sum_empty_iterable():
    def fn():
        return sum(())

    assert run_and_validate(fn) == 0


def test_sum_empty_iterable_with_start():
    def fn():
        return sum((), 7)

    assert run_and_validate(fn) == 7


def test_sum_empty_array():
    def fn():
        return sum(Array[int, 0]())

    assert run_and_validate(fn) == 0


def test_sum_runtime_start():
    def fn():
        s = 0
        for i in range(4):
            s += i
        return sum((1, 2, 3), s)

    assert run_and_validate(fn) == 12


def test_sum_runtime_values():
    def fn():
        return sum(Array(1, 2, 3))

    assert run_and_validate(fn) == 6


def test_sum_rejects_record_start_instead_of_mutating_it():
    # This used to compile and accumulate into `base` in place, silently mutating the caller's Record.
    def fn():
        base = Vec2(1, 2)
        sum((Vec2(1, 1), Vec2(2, 3)), base)
        return base.x

    with pytest.raises(CompilationError, match=r"sum\(\) only supports numeric values.*start value"):
        compile_fn(fn)


def test_sum_rejects_non_numeric_start_with_empty_iterable():
    # The loop never runs here, so only the start guard can reject it. This used to return the Record.
    def fn():
        return sum((), Vec2(1, 2)).x

    with pytest.raises(CompilationError, match=r"sum\(\) only supports numeric values.*start value"):
        compile_fn(fn)


def test_sum_rejects_non_numeric_elements():
    def fn():
        return sum((Vec2(1, 1), Vec2(2, 3))).x

    with pytest.raises(CompilationError, match=r"sum\(\) only supports numeric values.*iterable element"):
        compile_fn(fn)


def test_sum_rejects_non_numeric_runtime_elements():
    def fn():
        return sum(Array(Vec2(1, 1), Vec2(2, 3))).x

    with pytest.raises(CompilationError, match=r"sum\(\) only supports numeric values.*iterable element"):
        compile_fn(fn)


def test_sum_error_names_the_offending_type():
    def fn():
        return sum((Vec2(1, 1),)).x

    with pytest.raises(CompilationError, match="Vec2"):
        compile_fn(fn)


class _Color(IntEnum):
    RED = 1
    GREEN = 2
    BLUE = 3


# reversed() over an enum class must produce wrapped values, like enumerate() and zip() already do.


def test_reversed_enum_max():
    def fn():
        return max(reversed(_Color))

    assert run_and_validate(fn) == 3


def test_reversed_enum_indexing():
    # Plain Python's reversed() returns an iterator that is not subscriptable, so this is compiled-only.
    def fn():
        return reversed(_Color)[0] * 100 + reversed(_Color)[2]

    assert run_compiled(fn) == 301


def test_reversed_enum_for_loop():
    def fn():
        results = VarArray[int, 3].new()
        for c in reversed(_Color):
            results.append(c)
        return results

    assert list(run_and_validate(fn)) == [c.value for c in reversed(_Color)]


def test_iter_on_dict_raises_clear_error():
    def fn():
        return next(iter({1: 10, 2: 20}))

    with pytest.raises(CompilationError, match=r"dict .* compile-time construct .* no iterator"):
        compile_fn(fn)


def test_iter_on_set_raises_clear_error():
    def fn():
        return next(iter({1, 2}))

    with pytest.raises(CompilationError, match=r"set .* compile-time construct .* no iterator"):
        compile_fn(fn)


def test_iter_on_enum_class_raises_clear_error():
    def fn():
        return next(iter(_Color))

    with pytest.raises(CompilationError, match=r"enum class .* compile-time construct .* no iterator"):
        compile_fn(fn)


@pytest.mark.parametrize(
    "make_fn",
    [
        lambda: next(iter({1: 10})),
        lambda: next(iter({1, 2})),
        lambda: next(iter(_Color)),
    ],
    ids=["dict", "set", "enum"],
)
def test_iter_on_compile_time_iterable_mentions_alternative(make_fn):
    with pytest.raises(CompilationError, match="iterate over it directly"):
        compile_fn(make_fn)


# Error messages must not leak the internal per-value ConstantValue class name, which embeds a memory address.


def _error_message(fn) -> str:
    with pytest.raises(CompilationError) as exc_info:
        compile_fn(fn)
    return str(exc_info.value)


@pytest.mark.parametrize(
    ("make_fn", "expected_name"),
    [
        (lambda: iter(None), "NoneType"),
        (lambda: len(None), "NoneType"),
        (lambda: sum((), None), "NoneType"),
        (lambda: set(None), "NoneType"),
        (lambda: iter("abc"), "str"),
        (lambda: len(Vec2), "type"),
        (lambda: abs({1, 2}), "set"),
        (lambda: next(zip(None)), "NoneType"),
        (lambda: next(zip("abc")), "str"),
        (lambda: isinstance(1, {1, 2}), "set"),
        (lambda: issubclass(Vec2, {1: 2}), "dict"),
        # A tuple of classes is reported element by element, so a set nested in one must not leak either.
        (lambda: isinstance(1, ({1, 2},)), "set"),
        (lambda: max(Array(1, 2, 3), default={1: 2}), "dict"),
        # Operator diagnostics come from the visitor rather than a builtin, and leaked the same wrapper.
        (lambda: "abc" + 1, "str"),
        (lambda: -"abc", "str"),
        (lambda: "abc" < 1, "str"),  # noqa: PLR0133
        # A builtin alias in a classinfo tuple must be named as written, not by its internal shim.
        (lambda: isinstance(1, (dict, None)), "dict"),
        (lambda: isinstance(1, (set, None)), "set"),
        (lambda: isinstance(1, (type, None)), "type"),
    ],
    ids=[
        "iter-none",
        "len-none",
        "sum-none",
        "set-none",
        "iter-str",
        "len-class",
        "abs-set",
        "zip-none",
        "zip-str",
        "isinstance-set",
        "issubclass-dict",
        "isinstance-set-in-tuple",
        "max-default-dict",
        "binary-op",
        "unary-op",
        "comparison",
        "alias-dict-in-tuple",
        "alias-set-in-tuple",
        "alias-type-in-tuple",
    ],
)
def test_error_messages_use_readable_type_names(make_fn, expected_name):
    message = _error_message(make_fn)
    assert expected_name in message
    assert "Const[" not in message
    assert "0x" not in message


def test_assert_on_unconvertible_value_names_the_type():
    # assert routes through the same truthiness conversion as if/while, which had its own set of leaking messages.
    def fn():
        assert "hello"  # noqa: PLW0129
        return 1

    message = _error_message(fn)
    assert "str" in message
    assert "Const[" not in message
    assert "0x" not in message


# A set is iterated through its backing dict, so every compile-time iteration helper must unwrap it.
# These sums are order-insensitive on purpose, since a set has no defined iteration order.


def test_zip_over_sets():
    def fn():
        total = 0
        for a, b in zip({1, 2, 3}, {10, 20, 30}):  # noqa: B905
            total += a + b
        return total

    assert run_and_validate(fn) == 66


def test_zip_over_set_and_tuple():
    def fn():
        total = 0
        for a, b in zip({1, 2}, (10, 20)):  # noqa: B905
            total += a + b
        return total

    assert run_and_validate(fn) == 33


def test_zip_over_sets_pair_count():
    def fn():
        count = 0
        for _ in zip({1, 2, 3}, {10, 20, 30, 40}):  # noqa: B905
            count += 1
        return count

    assert run_and_validate(fn) == 3


def test_enumerate_over_set():
    def fn():
        total = 0
        for i, v in enumerate({10, 20, 30}):
            total += i + v
        return total

    assert run_and_validate(fn) == 63


def test_enumerate_over_set_with_start():
    def fn():
        total = 0
        for i, v in enumerate({10, 20, 30}, 1):
            total += i + v
        return total

    assert run_and_validate(fn) == 66


def test_enumerate_rejects_fractional_start():
    def fn(start):
        total = 0
        for i, value in enumerate(Array(10, 20), start):
            total += i + value
        return total

    with pytest.raises(TypeError):
        run_and_validate(fn, 1.5)


def test_fractional_enumerate_start_in_dead_runtime_branch_compiles():
    def fn(take_branch):
        if black_box_value(take_branch):
            for i, _value in enumerate(Array(10), 1.5):
                return i
        return 42

    assert run_and_validate(fn, False) == 42


def test_max_over_set():
    def fn():
        return max({3, 1, 2})

    assert run_and_validate(fn) == 3


def test_min_over_set():
    def fn():
        return min({3, 1, 2})

    assert run_and_validate(fn) == 1


def test_max_over_set_with_key():
    def fn():
        return max({3, 1, 2}, key=lambda v: -v)

    assert run_and_validate(fn) == 1


@pytest.mark.parametrize(
    "make_fn",
    [
        lambda: sum(map(lambda x: x, None)),  # noqa: C417
        lambda: sum(filter(None, None)),
        lambda: sum(x for x in zip(None)),
    ],
    ids=["map", "filter", "zip"],
)
def test_non_iterable_argument_reports_the_same_message(make_fn):
    # map and filter used to leak an internal AttributeError naming __iter__ where zip already said this.
    with pytest.raises(CompilationError, match="'NoneType' object is not iterable"):
        compile_fn(make_fn)


def test_reversed_rejects_set_like_python():
    # Plain Python raises TypeError here, so rejecting a set is correct rather than a gap in the family.
    def fn():
        return reversed({1, 2, 3})[0]

    with pytest.raises(CompilationError, match="'set' object is not reversible"):
        compile_fn(fn)


def test_next_over_array_iterator_two_results_live():
    def fn():
        it = iter(Array(2, 4, 6))
        return next(it) + next(it)

    assert run_and_validate(fn) == 6


def test_next_over_array_iterator_three_results_live():
    def fn():
        it = iter(Array(2, 4, 6))
        return next(it) + next(it) + next(it)

    assert run_and_validate(fn) == 12


def test_next_over_map_iterator_two_results_live():
    def fn():
        it = map(lambda v: v * 10, Array(1, 2, 3))  # noqa: C417
        return next(it) + next(it)

    assert run_and_validate(fn) == 30


def test_next_over_map_iterator_three_results_live():
    def fn():
        it = map(lambda v: v * 10, Array(1, 2, 3))  # noqa: C417
        return next(it) + next(it) + next(it)

    assert run_and_validate(fn) == 60


def test_next_over_range_iterator_two_results_live():
    def fn():
        it = iter(range(1, 10))
        return next(it) + next(it)

    assert run_and_validate(fn) == 3


def test_next_over_filtered_iterator_two_results_live():
    def fn():
        it = filter(lambda v: v % 2 == 0, Array(1, 2, 3, 4, 5, 6))
        return next(it) + next(it)

    assert run_and_validate(fn) == 6


def test_next_over_enumerate_iterator_two_results_live():
    def fn():
        it = enumerate(Array(10, 20, 30))
        first = next(it)
        second = next(it)
        return first[0] * 1000 + first[1] * 100 + second[0] * 10 + second[1]

    assert run_and_validate(fn) == 1030


def test_max_two_args():
    a, b = 3, 7

    def fn():
        return max(a, b)

    assert run_and_validate(fn) == 7


def test_max_two_args_reversed():
    a, b = 7, 3

    def fn():
        return max(a, b)

    assert run_and_validate(fn) == 7


def test_min_two_args():
    a, b = 3, 7

    def fn():
        return min(a, b)

    assert run_and_validate(fn) == 3


def test_min_two_args_reversed():
    a, b = 7, 3

    def fn():
        return min(a, b)

    assert run_and_validate(fn) == 3


def test_max_two_args_explicit_key_none():
    a, b = 3, 7

    def fn():
        return max(a, b, key=None)

    assert run_and_validate(fn) == 7


def test_max_two_args_explicit_key_none_reversed():
    a, b = 7, 3

    def fn():
        return max(a, b, key=None)

    assert run_and_validate(fn) == 7


def test_min_two_args_explicit_key_none():
    a, b = 3, 7

    def fn():
        return min(a, b, key=None)

    assert run_and_validate(fn) == 3


def test_min_two_args_explicit_key_none_reversed():
    a, b = 7, 3

    def fn():
        return min(a, b, key=None)

    assert run_and_validate(fn) == 3
