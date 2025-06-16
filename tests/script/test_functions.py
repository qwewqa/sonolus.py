from sonolus.script.array import Array
from sonolus.script.debug import debug_log
from tests.script.conftest import run_and_validate
from tests.script.test_record import Pair


def call_function(f, *args, **kwargs):
    return f(*args, **kwargs)


def test_simple_function_call():
    def a():
        return 1

    def fn():
        return a()

    assert run_and_validate(fn) == 1


def test_lambda_function_call():
    a = lambda: 1  # noqa: E731
    fn = lambda: a()  # noqa: PLW0108, E731

    assert run_and_validate(fn) == 1


def test_call_nested_function():
    def fn():
        def a():
            return 1

        return a()

    assert run_and_validate(fn) == 1


def test_indirect_call_nested_function():
    def fn():
        def a():
            return 1

        return call_function(a)

    assert run_and_validate(fn) == 1


def test_call_closure():
    def fn():
        x = 1

        def a():
            return x

        debug_log(a())
        debug_log(call_function(a))

        x = 2

        debug_log(a())
        debug_log(call_function(a))

        return a()

    assert run_and_validate(fn) == 2


def test_call_closure_with_default_args():
    def fn():
        x = 1

        def a(y=10):
            return x + y

        result = a()
        debug_log(result)
        return result

    assert run_and_validate(fn) == 11


def test_nested_lambda_closure():
    def fn():
        x = 1
        f = lambda y: x + y  # noqa: E731
        return f(2)

    assert run_and_validate(fn) == 3


def test_multiple_nested_functions():
    def fn():
        def outer(x):
            def middle(y):
                def inner(z):
                    return x + y + z

                return inner(3)

            return middle(2)

        return outer(1)

    assert run_and_validate(fn) == 6


def test_higher_order_function():
    def fn():
        def make_adder(x):
            return lambda y: x + y

        add_five = make_adder(5)
        return add_five(3)

    assert run_and_validate(fn) == 8


def test_pair_in_closure():
    def fn():
        p = Pair(1, 2)

        def get_sum():
            return p.first + p.second

        return get_sum()

    assert run_and_validate(fn) == 3


def test_nested_pair_manipulation():
    def fn():
        def make_pair_adder(p):
            def add_to_pair(x):
                return Pair(p.first + x, p.second + x)

            return add_to_pair

        initial_pair = Pair(1, 2)
        add_to_initial = make_pair_adder(initial_pair)
        result_pair = add_to_initial(3)
        return result_pair.first + result_pair.second

    assert run_and_validate(fn) == 9


def test_bool_returning_closure():
    def fn():
        threshold = 10

        def is_above_threshold(x):
            return x > threshold

        return is_above_threshold(15)

    assert run_and_validate(fn)


def test_arithmetic_in_closure():
    def fn():
        factor = 2.5

        def multiply(x):
            return factor * x

        return multiply(4.0)

    assert run_and_validate(fn) == 10.0


def test_nested_closure_with_record():
    def fn():
        y = 10

        def make_processor(base):
            def process(x):
                return Pair(0, x + y * base)

            return process

        processor = make_processor(10)
        result = processor(15)
        return result.second

    assert run_and_validate(fn)


def test_modify_closure_before_call():
    def fn():
        x = 1
        p = Pair(5, 10)

        def get_sum():
            return x + p.first + p.second

        first_result = get_sum()  # 1 + 5 + 10 = 16
        debug_log(first_result)

        x = 2
        p.first = 7
        p.second = 12

        second_result = get_sum()  # 2 + 7 + 12 = 21
        debug_log(second_result)

        return Array(first_result, second_result)  # [16, 21]

    assert run_and_validate(fn) == Array(16, 21)


def test_return_closure_with_modified_values():
    def fn():
        x = 1
        p = Pair(5, 10)

        def make_getter():
            def get_values():
                return x + p.first + p.second

            return get_values

        getter = make_getter()
        first_result = getter()  # 1 + 5 + 10 = 16

        x = 2
        p.first = 7
        p.second = 12

        second_result = getter()  # 2 + 7 + 12 = 21
        return Array(first_result, second_result)  # [16, 21]

    assert run_and_validate(fn) == Array(16, 21)


def test_pass_closure_after_modification():
    def fn():
        def apply_twice(f):
            return f() + f()

        x = 1
        p = Pair(5, 10)

        def get_sum():
            return x + p.first + p.second

        first_result = apply_twice(get_sum)  # (1+5+10) + (1+5+10) = 32

        x = 2
        p.first = 7
        p.second = 12

        second_result = apply_twice(get_sum)  # (2+7+12) + (2+7+12) = 42
        return Array(first_result, second_result)  # [32, 42]

    assert run_and_validate(fn) == Array(32, 42)


def test_nested_closure_pair_modification():
    def fn():
        p = Pair(1, 2)

        def make_modifier():
            def modify():
                p.first += 1
                p.second += 2
                return p.first + p.second

            return modify

        modifier = make_modifier()
        first = modifier()  # p becomes (2,4), returns 6
        second = modifier()  # p becomes (3,6), returns 9
        return Array(first, second)  # [6, 9]

    assert run_and_validate(fn) == Array(6, 9)


def test_multiple_closures_sharing_state():
    def fn():
        p = Pair(1, 2)

        def make_incrementer():
            def increment():
                p.first += 1
                p.second += 2
                return p.first

            return increment

        def make_getter():
            def get():
                return p.first + p.second

            return get

        incrementer = make_incrementer()
        getter = make_getter()

        first = getter()  # 1 + 2 = 3
        incrementer()  # p becomes (2,4)
        second = getter()  # 2 + 4 = 6
        incrementer()  # p becomes (3,6)
        third = getter()  # 3 + 6 = 9

        return Array(first, second, third)  # [3, 6, 9]

    assert run_and_validate(fn) == Array(3, 6, 9)


def test_higher_order_with_modified_closure():
    def fn():
        def apply_and_sum(f1, f2):
            return f1() + f2()

        x = 1
        y = 2

        def make_first():
            return lambda: x + y

        def make_second():
            return lambda: x * y

        f1 = make_first()
        f2 = make_second()

        first_result = apply_and_sum(f1, f2)  # (1+2) + (1*2) = 5

        x = 2
        y = 3

        second_result = apply_and_sum(f1, f2)  # (2+3) + (2*3) = 11

        return Array(first_result, second_result)  # [5, 11]

    assert run_and_validate(fn) == Array(5, 11)


def test_closure_with_pair_default():
    def fn():
        base = Pair(1, 2)

        def make_adder():
            def add(p=base):
                return p.first + p.second

            return add

        adder = make_adder()
        first = adder()  # 1 + 2 = 3

        base.first = 3
        base.second = 4

        second = adder()  # 3 + 4 = 7
        return Array(first, second)  # [3, 7]

    assert run_and_validate(fn) == Array(3, 7)


def test_closure_with_function_default():
    def fn():
        x = 1

        def default_func():
            return x * 2

        def make_processor():
            def process(f=default_func):
                return f() + x

            return process

        processor = make_processor()
        first = processor()  # (1*2) + 1 = 3

        x = 2
        second = processor()  # (2*2) + 2 = 6

        return Array(first, second)  # [3, 6]

    assert run_and_validate(fn) == Array(3, 6)


def test_nested_default_args():
    def fn():
        p = Pair(1, 2)

        def make_outer(default_value=10):
            def make_inner(multiplier=2):
                def compute(pair=p):
                    return (pair.first + pair.second) * multiplier + default_value

                return compute

            return make_inner

        f1 = make_outer()(3)(Pair(2, 3))  # (2+3)*3 + 10 = 25
        f2 = make_outer(20)()(p)  # (1+2)*2 + 20 = 26

        return Array(f1, f2)  # [25, 26]

    assert run_and_validate(fn) == Array(25, 26)


def test_default_args_with_modification():
    def fn():
        p1 = Pair(1, 2)
        p2 = Pair(3, 4)

        def make_calculator(base=p1):
            def calc(other=p2):
                return base.first + base.second + other.first + other.second

            return calc

        calculator = make_calculator()
        first = calculator()  # 1 + 2 + 3 + 4 = 10

        p1.first = 5
        p2.second = 6

        second = calculator()  # 5 + 2 + 3 + 6 = 16
        return Array(first, second)  # [10, 16]

    assert run_and_validate(fn) == Array(10, 16)


def test_default_function_modification():
    def fn():
        x = 1

        def initial_func():
            return x

        def make_processor(f=initial_func):
            def process(multiplier=2):
                return f() * multiplier

            return process

        processor = make_processor()
        first = processor()  # 1 * 2 = 2

        x = 3
        second = processor(3)  # 3 * 3 = 9

        return Array(first, second)  # [2, 9]

    assert run_and_validate(fn) == Array(2, 9)


def test_mixed_defaults_and_closure():
    def fn():
        base = Pair(1, 2)
        factor = 2

        def make_computer():
            def default_func(p):
                return p.first + p.second

            def compute(f=default_func, p=base, multiplier=factor):
                return f(p) * multiplier

            return compute

        computer = make_computer()
        first = computer()  # (1+2) * 2 = 6

        base.first = 3
        factor = 3  # This won't affect the default arg!

        second = computer(multiplier=4)  # (3+2) * 4 = 20
        return Array(first, second)  # [6, 20]

    assert run_and_validate(fn) == Array(6, 20)


def test_chain_of_defaults():
    def fn():
        p = Pair(1, 2)

        def make_base(pair=p):
            return pair.first + pair.second

        def make_multiplier(f=make_base):
            def multiply(factor=2):
                return f() * factor

            return multiply

        multiplier = make_multiplier()
        first = multiplier()  # (1+2) * 2 = 6

        p.first = 3
        second = multiplier(3)  # (3+2) * 3 = 15

        return Array(first, second)  # [6, 15]

    assert run_and_validate(fn) == Array(6, 15)


def test_nested_pair_default_modification():
    def fn():
        outer = Pair(1, 2)
        inner = Pair(3, 4)

        def make_outer(p1=outer):
            def make_inner(p2=inner):
                def compute(factor=2):
                    return (p1.first + p1.second + p2.first + p2.second) * factor

                return compute

            return make_inner

        computer = make_outer()(Pair(5, 6))
        first = computer()  # (1+2+5+6) * 2 = 28

        outer.first = 7
        inner.second = 8  # Shouldn't affect result since we passed different Pair

        second = computer(3)  # (7+2+5+6) * 3 = 60
        return Array(first, second)  # [28, 60]

    assert run_and_validate(fn) == Array(28, 60)


def test_changing_values_after_closure_creation():
    def fn():
        def make_func(x):
            def f():
                return x

            x *= 2

            return f

        f1 = make_func(1)
        f2 = make_func(3)

        return Array(f1(), f2())

    assert run_and_validate(fn) == Array(2, 6)


def test_nested_default_args_with_side_effects():
    def fn():
        counter = Pair(0, 0)

        def increment_and_return():
            counter.first += 1
            return counter.first

        def make_func(default_val=increment_and_return()):  # noqa: B008
            def inner(x=default_val):
                return x + increment_and_return()

            return inner

        f = make_func()
        first = f()
        second = f(10)
        return Array(first, second)

    assert run_and_validate(fn) == Array(3, 13)


def test_closure_sharing_between_functions():
    def fn():
        shared = Pair(1, 2)

        def make_modifier():
            def modify():
                shared.first *= 2
                shared.second += 3
                return shared.first + shared.second

            return modify

        def make_observer():
            def observe():
                return shared.first * shared.second

            return observe

        modifier = make_modifier()  # shared becomes (2, 5)
        observer = make_observer()  # shared is still (2, 5)

        return Array(modifier(), observer())

    assert run_and_validate(fn) == Array(7, 10)


def test_nested_function_redefinition():
    def fn():
        x = 1

        def make_outer():
            y = 2

            def make_inner():
                z = 3

                def inner():
                    return x + y + z

                z = 4
                return inner

            y = 3
            return make_inner()

        func = make_outer()
        first = func()  # 1 + 3 + 4 = 8
        x = 5
        second = func()  # 5 + 3 + 4 = 12

        return Array(first, second)  # [8, 12]

    assert run_and_validate(fn) == Array(8, 12)


def test_closure_with_conditional_modification():
    def fn():
        p = Pair(1, 2)

        def make_modifier(condition):
            if condition:
                p.first += 5

            def modify():
                if condition:
                    return p.first * 2
                else:
                    return p.second * 3

            if condition:
                p.second += 3
            return modify

        f1 = make_modifier(True)  # p becomes (6, 5)
        f2 = make_modifier(False)  # p unchanged

        return Array(f1(), f2())

    assert run_and_validate(fn) == Array(12, 15)


def test_mutable_default_args():
    def fn():
        def make_adder(pair=Pair(1, 2)):  # noqa: B008
            pair.first += 1
            pair.second += 2

            def add(new_pair=pair):  # Mutable default that gets modified
                new_pair.first += 1
                new_pair.second += 2
                return new_pair.first + new_pair.second

            return add

        adder = make_adder()  # pair becomes (2, 4)
        first = adder()  # pair becomes (3, 6), returns 9
        second = adder()  # pair becomes (4, 8), returns 12

        new_adder = make_adder()  # new pair becomes (5, 10)
        third = new_adder()  # new pair becomes (6, 12), returns 18

        return Array(first, second, third)

    assert run_and_validate(fn) == Array(9, 12, 18)


def test_nested_mutable_defaults():
    def fn():
        def outer(p1=Pair(1, 2)):  # noqa: B008
            p1.first += 1  # p1 becomes (2, 2)

            def inner(p2=p1):  # p2 references the same pair as p1
                def deepest(p3=p2):  # p3 references the same pair as p1 and p2
                    p3.second += 1
                    return p1.first * p2.second  # Same as p3.first * p3.second

                return deepest

            return inner()

        f1 = outer()  # Creates pair (2, 2)
        r1 = f1()  # Modifies to (2, 3), returns 6
        r2 = f1()  # Modifies to (2, 4), returns 8

        f2 = outer()  # Modifies to (3, 4)
        r3 = f2()  # Modifies to (3, 5), returns 15

        return Array(r1, r2, r3)

    assert run_and_validate(fn) == Array(6, 8, 15)


def test_mixed_mutable_immutable_defaults():
    def fn():
        def make_processor(immutable=1, mutable=Pair(1, 2)):  # noqa: B008
            def process(x=immutable, pair=mutable):
                pair.first += x
                pair.second += x
                return pair.first + pair.second

            return process

        proc = make_processor(2)  # immutable=2, creates mutable Pair(1, 2)
        first = proc()  # Pair becomes (3, 4), returns 7
        second = proc()  # Pair becomes (5, 6), returns 11
        third = proc(3)  # Pair becomes (8, 9), returns 17

        return Array(first, second, third)

    assert run_and_validate(fn) == Array(7, 11, 17)


def test_simple_decorator():
    def multiply_result(f):
        def wrapper():
            return f() * 2

        return wrapper

    def fn():
        @multiply_result
        def compute():
            return 5

        return compute()

    assert run_and_validate(fn) == 10


def test_decorator_with_arguments():
    def multiply_by(factor):
        def decorator(f):
            def wrapper():
                return f() * factor

            return wrapper

        return decorator

    def fn():
        @multiply_by(3)
        def compute():
            return 5

        return compute()

    assert run_and_validate(fn) == 15


def test_decorator_with_state():
    def fn():
        counter = Pair(0, 0)  # Using first for call count, second for sum

        def track_calls(f):
            def wrapper(*args):
                counter.first += 1
                result = f(*args)
                counter.second += result
                return result

            return wrapper

        @track_calls
        def add(x, y):  # noqa: FURB118
            return x + y

        first = add(2, 3)  # counter becomes (1, 5)
        second = add(4, 5)  # counter becomes (2, 14)

        return Array(first, second, counter.first, counter.second)

    assert run_and_validate(fn) == Array(5, 9, 2, 14)


def test_multiple_decorators():
    def fn():
        def double_result(f):
            def wrapper():
                return f() * 2

            return wrapper

        def add_five(f):
            def wrapper():
                return f() + 5

            return wrapper

        @double_result
        @add_five
        def compute():
            return 3

        return compute()  # (3 + 5) * 2 = 16

    assert run_and_validate(fn) == 16


def test_decorator_with_closure():
    def fn():
        multiplier = 2

        def multiply_by_closure(f):
            def wrapper():
                return f() * multiplier

            return wrapper

        @multiply_by_closure
        def compute():
            return 5

        first = compute()  # 5 * 2 = 10
        multiplier = 3
        second = compute()  # 5 * 3 = 15

        return Array(first, second)

    assert run_and_validate(fn) == Array(10, 15)


def test_decorator_with_mutable_state():
    def fn():
        state = Pair(1, 2)

        def modify_state(f):
            def wrapper():
                state.first += 1
                result = f()
                state.second += 2
                return result

            return wrapper

        @modify_state
        def get_sum():
            return state.first + state.second

        first = get_sum()  # state becomes (2, 2) then (2, 4), returns 4
        second = get_sum()  # state becomes (3, 4) then (3, 6), returns 7

        return Array(first, second)

    assert run_and_validate(fn) == Array(4, 7)


def test_decorator_factory_with_defaults():
    def fn():
        def create_multiplier(factor=2, offset=Pair(0, 0)):  # noqa: B008
            def decorator(f):
                def wrapper():
                    offset.first += 1
                    result = f() * factor + offset.first
                    return result

                return wrapper

            return decorator

        @create_multiplier()
        def compute1():
            return 5

        @create_multiplier(factor=3)
        def compute2():
            return 5

        first = compute1()  # 5 * 2 + 1 = 11
        second = compute1()  # 5 * 2 + 2 = 12
        third = compute2()  # 5 * 3 + 3 = 18

        return Array(first, second, third)

    assert run_and_validate(fn) == Array(11, 12, 18)


def test_nested_decorator_with_shared_state():
    def fn():
        shared = Pair(1, 2)

        def outer_decorator(f):
            def outer_wrapper():
                shared.first *= 2

                @inner_decorator
                def inner():
                    return f() + shared.first

                return inner()

            return outer_wrapper

        def inner_decorator(f):
            def inner_wrapper():
                shared.second *= 2
                return f() + shared.second

            return inner_wrapper

        @outer_decorator
        def compute():
            return 1

        first = compute()  # shared becomes (2, 4), returns 1 + 2 + 4 = 7
        second = compute()  # shared becomes (4, 8), returns 1 + 4 + 8 = 13

        return Array(first, second)

    assert run_and_validate(fn) == Array(7, 13)


def test_decorator_with_conditional_behavior():
    def fn():
        state = Pair(0, 0)

        def conditional_decorator(condition):
            def decorator(f):
                def wrapper():
                    if condition:
                        state.first += 1
                        return f() * 2
                    else:
                        state.second += 1
                        return f() * 3

                return wrapper

            return decorator

        @conditional_decorator(True)
        def compute1():
            return 5

        @conditional_decorator(False)
        def compute2():
            return 5

        return Array(compute1(), compute2(), state.first, state.second)

    assert run_and_validate(fn) == Array(10, 15, 1, 1)


def test_decorator_preserving_default_args():
    def fn():
        def preserve_defaults(f):
            def wrapper(x=None, pair=None):
                if x is None:
                    x = 1
                if pair is None:
                    pair = Pair(1, 2)
                pair.first += x
                return f(x=x, pair=pair)

            return wrapper

        @preserve_defaults
        def compute(x=1, pair=Pair(1, 2)):  # noqa: B008
            return pair.first + x

        first = compute()  # Uses defaults, pair becomes (2, 2), returns 3
        second = compute(2)  # New pair (3, 2), returns 5
        third = compute(pair=Pair(5, 5))  # Pair becomes (6, 5), returns 7

        return Array(first, second, third)

    assert run_and_validate(fn) == Array(3, 5, 7)


def multiply(func=None, factor=2):
    if func is None:

        def decorator(f):
            def wrapper(*args, **kwargs):
                return f(*args, **kwargs) * factor

            return wrapper

        return decorator

    def wrapper(*args, **kwargs):
        return func(*args, **kwargs) * factor

    return wrapper


def test_external_flexible_decorator():
    def fn():
        @multiply
        def compute1():
            return 5

        @multiply(factor=3)
        def compute2():
            return 5

        return Array(compute1(), compute2())  # [10, 15]

    assert run_and_validate(fn) == Array(10, 15)


def test_internal_flexible_decorator():
    def fn():
        state = Pair(1, 0)  # first: multiplier, second: call count

        def counter(func=None, factor=None):
            if func is None:

                def decorator(f):
                    return counter(f, factor)

                return decorator

            def wrapper(*args, **kwargs):
                actual_factor = state.first if factor is None else factor
                state.second += 1
                return func(*args, **kwargs) * actual_factor

            return wrapper

        @counter
        def compute1():
            return 5

        @counter(factor=3)
        def compute2():
            return 5

        r1 = compute1()  # 5 * 1 = 5
        r2 = compute2()  # 5 * 3 = 15
        calls1 = state.second  # 2 calls

        # Modify state
        state.first = 2

        r3 = compute1()  # 5 * 2 = 10
        r4 = compute2()  # 5 * 3 = 15
        calls2 = state.second  # 4 calls

        return Array(r1, r2, calls1, r3, r4, calls2)

    assert run_and_validate(fn) == Array(5, 15, 2, 10, 15, 4)


def test_flexible_decorator_with_mutable_state():
    def fn():
        def accumulate(func=None, storage=None):
            actual_storage = Pair(0, 0) if storage is None else storage

            if func is None:

                def decorator(f):
                    def wrapper(*args, **kwargs):
                        actual_storage.first += 1
                        result = f(*args, **kwargs)
                        actual_storage.second += result
                        return result

                    return wrapper

                return decorator

            def wrapper(*args, **kwargs):
                actual_storage.first += 1
                result = func(*args, **kwargs)
                actual_storage.second += result
                return result

            return wrapper

        shared_store = Pair(0, 0)  # first: call count, second: sum

        @accumulate(storage=shared_store)
        def compute1():
            return 5

        @accumulate()  # Uses its own Pair
        def compute2():
            return 3

        r1 = compute1()  # 5, shared_store becomes (1, 5)
        r2 = compute2()  # 3, different store becomes (1, 3)
        r3 = compute1()  # 5, shared_store becomes (2, 10)
        calls = shared_store.first  # 2 calls to compute1
        total = shared_store.second  # Sum of 10 from compute1

        return Array(r1, r2, r3, calls, total)

    assert run_and_validate(fn) == Array(5, 3, 5, 2, 10)


def test_nested_flexible_decorators():
    def fn():
        def multiply(func=None, factor=2):
            if func is None:

                def decorator(f):
                    def wrapper(*args, **kwargs):
                        return f(*args, **kwargs) * factor

                    return wrapper

                return decorator

            def wrapper(*args, **kwargs):
                return func(*args, **kwargs) * factor

            return wrapper

        def add(func=None, value=1):
            if func is None:

                def decorator(f):
                    def wrapper(*args, **kwargs):
                        return f(*args, **kwargs) + value

                    return wrapper

                return decorator

            def wrapper(*args, **kwargs):
                return func(*args, **kwargs) + value

            return wrapper

        @multiply
        @add(value=2)
        def compute1():
            return 5  # (5 + 2) * 2 = 14

        @multiply(factor=3)
        @add
        def compute2():
            return 5  # (5 + 1) * 3 = 18

        @add
        @multiply(factor=2)
        def compute3():
            return 5  # (5 * 2) + 1 = 11

        return Array(compute1(), compute2(), compute3())

    assert run_and_validate(fn) == Array(14, 18, 11)


def test_flexible_decorator_with_dynamic_state():
    def fn():
        state = Pair(1, 1)  # first: base value, second: current multiplier

        def dynamic(func=None, capture_state=False):
            if func is None:

                def decorator(f):
                    # Capture current state if requested
                    base = state.first if capture_state else None

                    def wrapper(*args, **kwargs):
                        multiplier = state.second
                        if base is not None:
                            return f(*args, **kwargs) * multiplier + base
                        return f(*args, **kwargs) * multiplier

                    return wrapper

                return decorator

            def wrapper(*args, **kwargs):
                return func(*args, **kwargs) * state.second

            return wrapper

        @dynamic
        def compute1():
            return 5

        @dynamic(capture_state=True)
        def compute2():
            return 5

        r1 = compute1()  # 5 * 1 = 5
        r2 = compute2()  # 5 * 1 + 1 = 6

        # Modify state
        state.first = 2
        state.second = 3

        r3 = compute1()  # 5 * 3 = 15
        r4 = compute2()  # 5 * 3 + 1 = 16 (uses captured base=1)

        return Array(r1, r2, r3, r4)

    assert run_and_validate(fn) == Array(5, 6, 15, 16)


def test_nested_function_with_complex_signature():
    def fn():
        def nested(pos_only_1, pos_only_2, /, pos_or_kw_1, pos_or_kw_2, *args, kw_only_1, kw_only_2=3, **kwargs):
            return pos_only_1 + pos_only_2 + pos_or_kw_1 + pos_or_kw_2 + kw_only_1 + kw_only_2

        return nested(1, 2, 3, 4, 12345, kw_only_1=5, kw_only_2=6, extra=7)

    assert run_and_validate(fn) == 21
