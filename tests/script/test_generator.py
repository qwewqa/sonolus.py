import random

import pytest

from sonolus.script.array import Array
from sonolus.script.containers import Box
from sonolus.script.debug import debug_log
from sonolus.script.internal.context import RuntimeChecks, ctx
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.visitor import compile_and_call
from sonolus.script.iterator import SonolusIterator
from sonolus.script.iterator import maybe_next as sonolus_maybe_next
from sonolus.script.maybe import Maybe, Nothing, Some
from sonolus.script.record import Record
from tests.script.conftest import run_and_validate, run_compiled


def runtime_false():
    return random.randrange(0, 1) != 0


def runtime_true():
    return random.randrange(0, 1) == 0


@meta_fn
def maybe_next_generator(iterator):
    if ctx():
        return compile_and_call(sonolus_maybe_next, iterator)
    try:
        return Some(next(iterator))
    except StopIteration:
        return Nothing


def test_simple_generator():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        for i in gen():
            debug_log(i)

    run_and_validate(fn)


def test_yield_in_nested_function_default_belongs_to_enclosing_generator():
    def fn():
        def gen():
            def nested(value=(yield 3)):
                return value

        total = 0
        for value in gen():
            total += value
        return total

    assert run_and_validate(fn) == 3


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


class StaticallyEmptyIterator(Record, SonolusIterator):
    """An iterator that is empty at compile time: `next` returns the literal Nothing."""

    v: float

    def next(self) -> Maybe[float]:
        return Nothing


class StaticallyEmpty(Record):
    v: float

    def __iter__(self):
        return StaticallyEmptyIterator(self.v)


class StaticallyNonemptyIterator(Record, SonolusIterator):
    value: float

    def next(self) -> Maybe[float]:
        return Some(self.value)


class StaticallyNonempty(Record):
    value: float

    def __iter__(self):
        return StaticallyNonemptyIterator(self.value)


class NonMaybeIterator(Record, SonolusIterator):
    def next(self):
        return 1


class NonMaybeIterable(Record):
    def __iter__(self):
        return NonMaybeIterator()


class SynthesizedIterable(Record):
    def __getattr__(self, name):
        if name == "__iter__":
            return lambda: iter(Array(1))
        raise AttributeError(name)


def test_getattr_does_not_supply_implicit_iteration_protocol():
    def fn():
        for _ in SynthesizedIterable():
            return 1
        return 0

    with pytest.raises(TypeError, match="'SynthesizedIterable' object is not iterable"):
        run_and_validate(fn)


def test_yield_from_requires_next_to_return_maybe():
    def gen():
        yield from NonMaybeIterable()

    def fn():
        for _ in gen():
            pass

    with pytest.raises(CompilationError, match=r"Iterator\.next\(\) returned 'Num', expected Maybe"):
        run_compiled(fn)


def test_for_over_statically_nonempty_iterator_has_no_exhaustion_path():
    def fn():
        for value in StaticallyNonempty(3):
            return value
        return Box(1)

    assert run_and_validate(fn) == 3


def test_yield_from_statically_nonempty_iterator_has_no_later_yield_path():
    def fn():
        def gen():
            yield from StaticallyNonempty(3)
            yield Box(1)

        for value in gen():
            return value
        return 0

    assert run_and_validate(fn) == 3


def test_genexpr_over_statically_nonempty_iterator_has_no_exhaustion_path():
    def fn():
        def gen():
            yield from (3 for _ in StaticallyNonempty(0))
            yield Box(1)

        for value in gen():
            return value
        return 0

    assert run_and_validate(fn) == 3


def test_yield_from_statically_infinite_generator_has_no_later_yield_path():
    def fn():
        def infinite():
            while True:
                yield 3

        def gen():
            yield from infinite()
            yield Box(1)

        for value in gen():
            return value
        return 0

    assert run_and_validate(fn) == 3


def test_yield_from_empty_zip_yields_nothing():
    # Each of these delegation tests sums the yielded values rather than counting them: a delegating
    # generator that binds its yield to a statically empty iterator's absent value still compiles when the
    # value is ignored, and only reading it shows the element type is wrong.
    def fn():
        def gen():
            yield from zip()

        total = 0.0
        for v in gen():
            total += v
        return total

    assert run_and_validate(fn) == 0.0


def test_yield_from_statically_empty_iterator_yields_nothing():
    def fn():
        def gen():
            yield from StaticallyEmpty(1.0)

        total = 0.0
        for v in gen():
            total += v
        return total

    assert run_and_validate(fn) == 0.0


def test_yield_from_generator_with_no_reachable_yield_yields_nothing():
    def fn(enabled):
        def maybe_yields():
            if enabled:
                yield 1.0
                yield 2.0

        def gen():
            yield from maybe_yields()

        total = 0.0
        for v in gen():
            total += v
        return total

    assert run_and_validate(fn, 0) == 0.0
    assert run_and_validate(fn, 1) == 3.0


def test_yield_from_statically_empty_iterator_then_yield():
    # The delegation is abandoned mid-expression, so the following yield is the one that pins that what it
    # is abandoned into stays usable.
    def fn():
        def gen():
            yield from StaticallyEmpty(1.0)
            yield 5.0

        total = 0.0
        for v in gen():
            total += v
        return total

    assert run_and_validate(fn) == 5.0


def test_yield_from_runtime_empty_generator_yields_nothing():
    # The control for the four above: this delegation is empty only at runtime, so it must keep its state
    # machine instead of being compiled away.
    def fn(limit):
        def inner():
            for v in Array(1.0, 2.0, 3.0):
                if v <= limit:
                    yield v

        def outer():
            yield from inner()

        total = 0.0
        for v in outer():
            total += v
        return total

    assert run_and_validate(fn, 0.0) == 0.0
    assert run_and_validate(fn, 2.0) == 3.0


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


def test_generator_transitive_capture_rebound_between_next_calls_is_rejected():
    def fn():
        x = 1

        def gen():
            get_x = lambda: x  # noqa: E731
            yield get_x()
            yield get_x()

        iterator = gen()
        first = next(iterator)
        x = 2
        return first + next(iterator)

    # Plain Python permits the rebind, but compiled generators require every captured binding to keep its
    # definition between resumptions.
    with pytest.raises(CompilationError, match="Binding 'x' has been modified since the generator was created"):
        run_compiled(fn)


def test_generator_capture_propagates_through_externally_defined_closure():
    def fn():
        x = 1
        get_x = lambda: x  # noqa: E731

        def gen():
            yield get_x()
            yield get_x()

        iterator = gen()
        first = next(iterator)
        x = 2
        return first + next(iterator)

    # Plain Python permits the rebind; this pins the compiled generator binding restriction through an
    # externally defined closure.
    with pytest.raises(CompilationError, match="Binding 'x' has been modified since the generator was created"):
        run_compiled(fn)


def test_external_closure_read_does_not_freeze_its_owner_binding():
    def fn():
        x = 1
        get_x = lambda: x  # noqa: E731
        first = get_x()
        x = 2
        return first + x

    assert run_and_validate(fn) == 3


def test_generator_does_not_capture_shadowed_binding_from_unrelated_closure_owner():
    def fn():
        y = 1

        def make_get_y():
            y = 2
            return lambda: y

        get_y = make_get_y()

        def gen():
            yield get_y()
            yield get_y()

        result = y
        for value in gen():
            result += value
        return result

    assert run_and_validate(fn) == 5


def test_outer_generator_does_not_capture_binding_used_only_by_nested_generator():
    def fn():
        x = 1

        def gen():
            inner = (x for _ in Array(1))
            yield inner
            yield inner

        for _ in gen():
            x = 2
        return 1

    assert run_and_validate(fn) == 1


def test_outer_generator_captures_binding_when_it_consumes_nested_generator():
    def fn():
        x = 1

        def gen():
            yield from (x for _ in Array(1, 1))

        iterator = gen()
        next(iterator)
        x = 2
        return next(iterator)

    # Plain Python permits the rebind; consuming the nested generator makes its dependency part of the outer
    # compiled generator's binding restriction.
    with pytest.raises(CompilationError, match="Binding 'x' has been modified since the generator was created"):
        run_compiled(fn)


def test_generator_does_not_capture_local_from_transient_callback_frame():
    def fn():
        def helper():
            z = 1
            get_z = lambda: z  # noqa: E731
            get_z()
            z = 2
            return 5

        def gen():
            yield helper()
            yield helper()

        iterator = gen()
        return next(iterator)

    assert run_and_validate(fn) == 5


def test_generator_does_not_inherit_nested_generator_capture_from_transient_callback():
    def fn():
        def helper():
            z = 1

            def inner():
                yield z

            value = next(inner())
            z = 2
            return value

        def outer():
            yield helper()
            yield helper()

        result = 0
        for value in outer():
            result += value
        return result

    assert run_and_validate(fn) == 2


def _generator_reading_callback(reader):
    yield reader()
    yield reader()


def test_module_level_generator_tracks_callback_closure_owner():
    def fn():
        x = 1
        reader = lambda: x  # noqa: E731
        iterator = _generator_reading_callback(reader)
        first = next(iterator)
        x = 2
        return first + next(iterator)

    # Plain Python permits the rebind; this pins the compiled generator binding restriction when the generator
    # has no lexical parent and reaches the binding through a callback.
    with pytest.raises(CompilationError, match="Binding 'x' has been modified since the generator was created"):
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


def test_generator_single_next_succeeds_with_runtime_checks():
    def fn():
        def gen():
            yield 1

        return next(gen())

    assert run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE) == 1


def test_generator_single_for_loop_succeeds_with_runtime_checks():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        result = 0
        for value in gen():
            result = result * 10 + value
        return result

    assert run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE) == 123


def test_generator_reached_next_call_twice_terminates_with_runtime_checks():
    def fn():
        def gen():
            yield 1
            yield 2

        iterator = gen()

        def advance():
            return next(iterator)

        first = advance()
        return first * 10 + advance()

    assert run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE) == 0
    run_compiled(fn, runtime_checks=RuntimeChecks.NONE)


def test_generator_two_next_calls_terminate_with_runtime_checks():
    def fn():
        def gen():
            yield 1
            yield 2

        iterator = gen()
        first = next(iterator)
        return first * 10 + next(iterator)

    assert run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE) == 0
    run_compiled(fn, runtime_checks=RuntimeChecks.NONE)


def test_generator_next_then_for_loop_terminates_with_runtime_checks():
    def fn():
        def gen():
            yield 1
            yield 2

        iterator = gen()
        result = next(iterator)
        for value in iterator:
            result += value
        return result

    assert run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE) == 0
    run_compiled(fn, runtime_checks=RuntimeChecks.NONE)


def test_generator_two_loops_terminate_with_runtime_checks():
    def fn():
        def gen():
            yield 1
            yield 2

        iterator = gen()
        result = 0
        for value in iterator:
            result += value
            break
        for value in iterator:
            result += value
        return result

    assert run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE) == 0
    run_compiled(fn, runtime_checks=RuntimeChecks.NONE)


def test_distinct_generator_instances_can_use_different_next_calls():
    def fn():
        def gen(value):
            yield value

        first = gen(1)
        second = gen(2)
        return next(first) * 10 + next(second)

    assert run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE) == 12


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


def test_generator_return_nonnone_constant_rejected():
    def fn():
        def gen():
            yield 1
            return 5  # noqa: B901

        for i in gen():
            debug_log(i)

    with pytest.raises(CompilationError, match="Generator function return statements must return None"):
        run_compiled(fn)


def test_nested_loops_over_one_array_iterator_match_python():
    # The array case is not affected: elements have distinct storage, so nothing is overwritten.
    def fn():
        it = iter(Array(1, 2, 3, 4))
        for a in it:
            for b in it:
                debug_log(b)
                break
            debug_log(a)
        return 0

    run_and_validate(fn)


def test_exhausted_compile_time_zip_repeats_pinned():
    # run_compiled, not run_and_validate: accepted divergence. zip/enumerate/reversed over a compile-time
    # construct produce a tuple rather than a one-shot iterator, so a second loop repeats the sequence where
    # plain Python yields nothing.
    def fn():
        it = zip((1, 2), (10, 20), strict=False)
        for a, b in it:
            debug_log(a + b)
        for a, b in it:
            debug_log(a + b)
        return 0

    logs = []
    run_compiled(fn, log_callback=logs.append)
    assert logs == [11, 22, 11, 22]


def test_lambda_yielded_by_host_generator_is_locatable():
    # Regression: the source-finding visitor must traverse into a lambda that is itself a `yield`
    # expression of a plain (non-compiled-subset) host generator, or the lambda's AST node is never
    # collected and calling it from compiled code fails to locate its source.
    def make_callback():
        yield lambda: 42

    callback = next(make_callback())

    def fn():
        return callback()

    assert run_and_validate(fn) == 42


def test_lambda_yielded_from_host_generator_is_locatable():
    # Same regression via `yield from`.
    def make_callback():
        yield from [lambda: 43]

    callback = next(make_callback())

    def fn():
        return callback()

    assert run_and_validate(fn) == 43


def test_lambda_yielded_from_compiled_generator_captures_current_local():
    def fn():
        def gen():
            value = 7
            yield lambda: value

        return next(gen())()

    assert run_and_validate(fn) == 7


def test_yield_from_lambda_captures_current_generator_local():
    def fn():
        def gen():
            value = 8
            yield from (lambda: value,)

        return next(gen())()

    assert run_and_validate(fn) == 8


def test_lambda_from_once_advanced_generator_captures_suspended_local():
    def fn():
        def gen():
            value = 1
            callback = lambda: value  # noqa: E731
            yield callback
            value = 2

        return next(gen())()

    assert run_and_validate(fn) == 1


def test_nested_function_from_once_advanced_generator_captures_suspended_local():
    def fn():
        def gen():
            value = 1

            def callback():
                return value

            yield callback
            value = 2

        return next(gen())()

    assert run_and_validate(fn) == 1


def test_callback_yielded_from_once_advanced_generator_captures_suspended_local():
    def fn():
        def gen():
            value = 1
            yield from (lambda: value,)
            value = 2

        return next(gen())()

    assert run_and_validate(fn) == 1


def test_nested_generator_capturing_suspended_generator_local_is_rejected():
    def fn():
        def outer():
            value = 1

            def inner():
                yield value

            yield inner()
            value = 2

        return next(next(outer()))

    with pytest.raises(
        CompilationError,
        match="Nested generator captures changing local 'value' from a suspended generator, which is not supported",
    ):
        run_compiled(fn)


def test_genexpr_capturing_suspended_generator_local_is_rejected():
    def fn():
        def outer():
            value = 1
            yield (value for _ in Array(1))
            value = 2

        return next(next(outer()))

    with pytest.raises(
        CompilationError,
        match="Nested generator captures changing local 'value' from a suspended generator, which is not supported",
    ):
        run_compiled(fn)


def test_nested_generator_capturing_active_generator_local():
    def fn():
        def outer():
            value = 1

            def inner():
                yield value

            yield next(inner())

        return next(outer())

    assert run_and_validate(fn) == 1


def test_nested_generator_capturing_invariant_suspended_generator_local():
    def fn():
        def outer():
            value = (1,)

            def inner():
                yield value[0]

            yield inner()

        return next(next(outer()))

    assert run_and_validate(fn) == 1


def test_repeated_nested_generator_advance_capturing_changing_suspended_local_is_rejected():
    def fn():
        def outer():
            value = 1

            def inner():
                yield value
                yield value

            iterator = inner()
            yield iterator
            value = 2
            yield iterator

        outer_iterator = outer()
        inner_iterator = next(outer_iterator)
        first = next(inner_iterator)
        if runtime_true():
            next(outer_iterator)
        return first * 10 + next(inner_iterator)

    with pytest.raises(
        CompilationError,
        match="Nested generator captures changing local 'value' from a suspended generator, which is not supported",
    ):
        run_compiled(fn)


def test_callback_from_once_advanced_generator_does_not_read_future_unbound_local():
    def fn():
        def gen():
            callback = lambda: value  # noqa: E731
            yield callback
            value = 2

        return next(gen())()

    with pytest.raises(
        CompilationError,
        match="cannot access free variable 'value' where it is not associated with a value in enclosing scope",
    ):
        run_compiled(fn)


def test_callback_from_once_advanced_generator_captures_reference_binding_at_first_suspension():
    def fn():
        def gen():
            value = (1,)
            callback = lambda: value[0]  # noqa: E731
            yield callback
            value = (2,)
            yield callback

        return next(gen())()

    assert run_and_validate(fn) == 1


@pytest.mark.parametrize(("condition", "expected"), [(runtime_false, 2), (runtime_true, 1)])
def test_once_advanced_generator_selects_runtime_reachable_suspension(condition, expected):
    def fn():
        def gen():
            value = 1
            callback = lambda: value  # noqa: E731
            if condition():
                yield callback
            value = 2
            yield callback

        return next(gen())()

    assert run_and_validate(fn) == expected


def test_runtime_ambiguous_reference_binding_reports_conflict():
    def fn():
        def gen():
            value = (1,)
            callback = lambda: value[0]  # noqa: E731
            if runtime_false():
                yield callback
            value = (2,)
            yield callback

        return next(gen())()

    with pytest.raises(
        CompilationError,
        match="Binding 'value' has multiple conflicting definitions or may not be guaranteed to be defined",
    ):
        run_compiled(fn)


def test_runtime_ambiguous_future_unbound_binding_reports_conflict():
    def fn():
        def gen():
            callback = lambda: value  # noqa: E731
            if runtime_false():
                yield callback
            value = 2
            yield callback

        return next(gen())()

    with pytest.raises(
        CompilationError,
        match="Binding 'value' has multiple conflicting definitions or may not be guaranteed to be defined",
    ):
        run_compiled(fn)


def test_once_advanced_yield_from_propagates_reference_suspension():
    def fn():
        def inner():
            value = (1,)
            callback = lambda: value[0]  # noqa: E731
            yield callback
            value = (2,)
            yield callback

        def outer():
            yield from inner()

        return next(outer())()

    assert run_and_validate(fn) == 1


def test_once_advanced_multilevel_yield_from_propagates_reference_suspension():
    def fn():
        def inner():
            value = (1,)
            callback = lambda: value[0]  # noqa: E731
            yield callback
            value = (2,)
            yield callback

        def middle():
            yield from inner()

        def outer():
            yield from middle()

        return next(outer())()

    assert run_and_validate(fn) == 1


def test_once_advanced_yield_from_propagates_branch_skipped_suspension():
    def fn():
        def inner():
            value = 1
            callback = lambda: value  # noqa: E731
            if runtime_false():
                yield callback
            value = 2
            yield callback

        def outer():
            yield from inner()

        return next(outer())()

    assert run_and_validate(fn) == 2


def test_yield_from_propagates_runtime_ambiguous_reference_conflict():
    def fn():
        def inner():
            value = (1,)
            callback = lambda: value[0]  # noqa: E731
            if runtime_false():
                yield callback
            value = (2,)
            yield callback

        def outer():
            yield from inner()

        return next(outer())()

    with pytest.raises(
        CompilationError,
        match="Binding 'value' has multiple conflicting definitions or may not be guaranteed to be defined",
    ):
        run_compiled(fn)


def test_generator_loop_callback_uses_runtime_suspension_binding():
    def fn():
        def gen():
            value = 1
            callback = lambda: value  # noqa: E731
            yield callback
            value = 2
            yield callback

        result = 0
        for callback in gen():
            result = result * 10 + callback()
        return result

    assert run_and_validate(fn) == 12


def test_statically_nonempty_false_filtered_generator_terminates_on_consumption():
    # There is no Python oracle because advancing this deliberately invalid iterator never makes progress.
    def fn():
        def gen():
            yield from (1 for _ in StaticallyNonempty(0) if False)

        return next(gen())

    assert run_compiled(fn, runtime_checks=RuntimeChecks.NONE) == 0
    assert run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE) == 0
    assert run_compiled(fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE) == 0
