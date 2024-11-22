from sonolus.script.array import Array
from sonolus.script.debug import debug_log
from tests.script.conftest import validate_dual_run
from tests.script.test_record import Pair


def call_function(f, *args, **kwargs):
    return f(*args, **kwargs)


def test_simple_function_call():
    def a():
        return 1

    def fn():
        return a()

    assert validate_dual_run(fn) == 1


def test_lambda_function_call():
    a = lambda: 1  # noqa: E731
    fn = lambda: a()  # noqa: PLW0108, E731

    assert validate_dual_run(fn) == 1


def test_call_nested_function():
    def fn():
        def a():
            return 1

        return a()

    assert validate_dual_run(fn) == 1


def test_indirect_call_nested_function():
    def fn():
        def a():
            return 1

        return call_function(a)

    assert validate_dual_run(fn) == 1


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

    assert validate_dual_run(fn) == 2


def test_call_closure_with_default_args():
    def fn():
        x = 1

        def a(y=10):
            return x + y

        result = a()
        debug_log(result)
        return result

    assert validate_dual_run(fn) == 11


def test_nested_lambda_closure():
    def fn():
        x = 1
        f = lambda y: x + y  # noqa: E731
        return f(2)

    assert validate_dual_run(fn) == 3


def test_multiple_nested_functions():
    def fn():
        def outer(x):
            def middle(y):
                def inner(z):
                    return x + y + z

                return inner(3)

            return middle(2)

        return outer(1)

    assert validate_dual_run(fn) == 6


def test_higher_order_function():
    def fn():
        def make_adder(x):
            return lambda y: x + y

        add_five = make_adder(5)
        return add_five(3)

    assert validate_dual_run(fn) == 8


def test_pair_in_closure():
    def fn():
        p = Pair(1, 2)

        def get_sum():
            return p.first + p.second

        return get_sum()

    assert validate_dual_run(fn) == 3


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

    assert validate_dual_run(fn) == 9


def test_bool_returning_closure():
    def fn():
        threshold = 10

        def is_above_threshold(x):
            return x > threshold

        return is_above_threshold(15)

    assert validate_dual_run(fn)


def test_arithmetic_in_closure():
    def fn():
        factor = 2.5

        def multiply(x):
            return factor * x

        return multiply(4.0)

    assert validate_dual_run(fn) == 10.0


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

    assert validate_dual_run(fn)


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

    assert validate_dual_run(fn) == Array(16, 21)


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

    assert validate_dual_run(fn) == Array(16, 21)


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

    assert validate_dual_run(fn) == Array(32, 42)


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

    assert validate_dual_run(fn) == Array(6, 9)


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

    assert validate_dual_run(fn) == Array(3, 6, 9)


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

    assert validate_dual_run(fn) == Array(5, 11)


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

    assert validate_dual_run(fn) == Array(3, 7)


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

    assert validate_dual_run(fn) == Array(3, 6)


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

    assert validate_dual_run(fn) == Array(25, 26)


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

    assert validate_dual_run(fn) == Array(10, 16)


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

    assert validate_dual_run(fn) == Array(2, 9)


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

    assert validate_dual_run(fn) == Array(6, 20)


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

    assert validate_dual_run(fn) == Array(6, 15)


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

    assert validate_dual_run(fn) == Array(28, 60)
