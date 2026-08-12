import pytest

from sonolus.script.array import Array
from sonolus.script.containers import Box
from sonolus.script.debug import debug_log
from sonolus.script.internal.error import CompilationError
from sonolus.script.iterator import SonolusIterator
from sonolus.script.maybe import Maybe, Nothing, Some
from sonolus.script.record import Record
from tests.script.conftest import run_and_validate, run_compiled


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


def test_generator_over_nested_array_breaks():
    def fn():
        arr = Array(Array(1, 2), Array(3, 4))

        def gen():
            for sub_arr in arr:
                for i in sub_arr:
                    yield i * 2
                yield 123

        iterator = gen()
        for i in iterator:
            debug_log(i)
            break
        for i in iterator:
            debug_log(i * 2)

    run_and_validate(fn)


def test_generator_over_nested_tuple_breaks():
    def fn():
        tup = ((1, 2), (3, 4))

        def gen():
            for sub_tup in tup:
                for i in sub_tup:
                    yield i * 2
                yield 123

        iterator = gen()
        for i in iterator:
            debug_log(i)
            break
        for i in iterator:
            debug_log(i * 2)

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


def test_yield_from_requires_next_to_return_maybe():
    def gen():
        yield from NonMaybeIterable()

    def fn():
        for _ in gen():
            pass

    with pytest.raises(CompilationError, match="Iterator next must return a Maybe"):
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


def test_closure_called_after_it_is_yielded_does_not_become_a_generator_capture():
    def fn():
        x = 1

        def gen():
            get_x = lambda: x  # noqa: E731
            yield get_x
            yield get_x

        iterator = gen()
        get_x = next(iterator)
        first = get_x()
        x = 2
        next(iterator)
        return first

    assert run_and_validate(fn) == 1


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

        iterator = gen()
        return next(iterator) + next(iterator) + y

    assert run_and_validate(fn) == 5


def test_outer_generator_does_not_capture_binding_used_only_by_nested_generator():
    def fn():
        x = 1

        def gen():
            inner = (x for _ in Array(1))
            yield inner
            yield inner

        iterator = gen()
        next(iterator)
        x = 2
        next(iterator)
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

        iterator = outer()
        return next(iterator) + next(iterator)

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


def test_generator_with_next():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        iterator = gen()
        debug_log(next(iterator))
        debug_log(next(iterator))
        debug_log(next(iterator))

    run_and_validate(fn)


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


def test_generator_two_next_results_live_in_one_expression():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        g = gen()
        return next(g) + next(g)

    assert run_and_validate(fn) == 3


def test_generator_three_next_results_live_in_one_expression():
    def fn():
        def gen():
            yield 2
            yield 4
            yield 6

        g = gen()
        return next(g) + next(g) + next(g)

    assert run_and_validate(fn) == 12


def test_generator_next_results_held_in_locals():
    def fn():
        def gen():
            yield 2
            yield 4
            yield 6

        g = gen()
        a = next(g)
        b = next(g)
        c = next(g)
        debug_log(a)
        debug_log(b)
        debug_log(c)
        return a * 100 + b * 10 + c

    assert run_and_validate(fn) == 246


def test_generator_next_results_live_across_branch():
    def fn():
        def gen():
            yield 5
            yield 1

        g = gen()
        first = next(g)
        second = next(g)
        if first > second:
            return first - second
        return second - first

    assert run_and_validate(fn) == 4


def test_generator_next_results_in_tuple():
    def fn():
        def gen():
            yield 3
            yield 7

        g = gen()
        pair = (next(g), next(g))
        return pair[0] * 10 + pair[1]

    assert run_and_validate(fn) == 37


def test_genexpr_two_next_results_live_in_one_expression():
    def fn():
        g = (v * 10 for v in Array(1, 2, 3))
        return next(g) + next(g)

    assert run_and_validate(fn) == 30


def test_genexpr_three_next_results_live_in_one_expression():
    def fn():
        g = (v * 10 for v in Array(1, 2, 3))
        return next(g) + next(g) + next(g)

    assert run_and_validate(fn) == 60


def test_generator_next_interleaved_with_consumption_unchanged():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        g = gen()
        debug_log(next(g))
        debug_log(next(g))
        debug_log(next(g))

    run_and_validate(fn)


def test_generator_next_then_for_loop_unchanged():
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3
            yield 4

        g = gen()
        total = next(g)
        for v in g:
            total += v
        return total

    assert run_and_validate(fn) == 10


def test_generator_next_exhausted_still_errors():
    def fn():
        def gen():
            yield 1

        g = gen()
        debug_log(next(g))
        debug_log(next(g))

    with pytest.raises(StopIteration):
        run_and_validate(fn)


def test_generator_yielding_record_next_results_alias_pinned():
    # Pinned limitation, not a bug report: the fix copies Num payloads only. For a reference-type payload
    # `_get_` (and so `_get_readonly_`) returns self by contract, so two live next() results still view the
    # generator's single yield storage. Compiled-only assertion: plain Python would give 24 here, so
    # run_and_validate cannot be used.
    def fn():
        def gen():
            for i in range(1, 3):
                yield Box(i * 2)

        g = gen()
        a = next(g)
        b = next(g)
        return a.value * 10 + b.value

    assert run_compiled(fn) == 44


def test_generator_next_results_via_enumerate_alias_pinned():
    # `enumerate`/`zip` wrap the payload in a TupleImpl, which is a TransientValue: its `_get_` (and so
    # `_get_readonly_`) returns self, so the Num elements inside are never snapshotted and both live results
    # still read the generator's latest yield.
    def fn():
        def gen():
            yield 1
            yield 2

        it = enumerate(gen())
        a = next(it)
        b = next(it)
        return a[1] * 10 + b[1]

    assert run_compiled(fn) == 22


def test_generator_next_results_via_zip_alias_pinned():
    # Same pinned TupleImpl limitation, via zip.
    def fn():
        def gen():
            yield 1
            yield 2

        it = zip(gen(), Array(7, 8), strict=False)
        a = next(it)
        b = next(it)
        return a[0] * 10 + b[0]

    assert run_compiled(fn) == 22


def test_nested_loops_over_one_generator_alias_pinned():
    # run_compiled, not run_and_validate: this is an accepted divergence. A generator reuses one location for the
    # value it yields, so the inner loop's advance overwrites the outer loop's variable. Plain Python logs
    # [2, 1, 4, 3]; this is the documented behaviour in concepts/constructs.md.
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3
            yield 4

        it = gen()
        for a in it:
            for b in it:
                debug_log(b)
                break
            debug_log(a)
        return 0

    logs = []
    run_compiled(fn, log_callback=logs.append)
    assert logs == [2, 2, 4, 4]


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


def test_generator_consumed_by_two_sequential_loops_matches_python():
    # Reuse is only a problem while an earlier value is still live; back-to-back loops are fine.
    def fn():
        def gen():
            yield 1
            yield 2
            yield 3

        total = 0
        it = gen()
        for x in it:
            total += x
        for x in it:
            total += x * 10
        return total

    assert run_and_validate(fn) == 6


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
