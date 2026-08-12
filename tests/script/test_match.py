"""Test cases intended to cover more complex control flow."""

from enum import IntEnum

import pytest

from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from sonolus.script.debug import assert_true, debug_log
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.range import Range
from sonolus.script.num import Num
from sonolus.script.record import Record
from tests.script.conftest import run_and_validate, run_compiled
from tests.script.test_flow import black_box_value
from tests.script.test_record import Pair


class Pair2[T, U](Record):
    first: T
    second: U


class Pair3[T, U](Record):
    first: T
    second: U


class Color(IntEnum):
    RED = 1
    GREEN = 2
    BLUE = 3


class Point(Record):
    x: Num
    y: Num


class Circle(Record):
    center: Point
    radius: Num


class Rectangle(Record):
    top_left: Point
    bottom_right: Point


class Shape[Kind, Data](Record):
    kind: Kind
    data: Data


def test_match_pair_and_num_checking():
    a = Pair(1, 2)
    b = 3
    c = Pair2(4, 5)
    d = "hello"

    def m(x):
        match x:
            case Pair(a, b):
                debug_log(a)
                debug_log(b)
            case Num(n):
                debug_log(n)
            case Pair2(a, b):
                debug_log(a + b)
                debug_log(a - b)
            case _:
                debug_log(-1)
        debug_log(123)

    def fn():
        m(a)
        m(b)
        m(c)
        m(d)

    run_and_validate(fn)


def test_match_shapes_and_collections_patterns():
    p1 = Point(1, 2)
    p2 = Point(3, 4)
    c = Circle(p1, 5)
    r = Rectangle(p1, p2)
    color = Color.RED
    num = 42
    tpl = (1, 2, 3)
    text = "test"

    def m(x):
        match x:
            case Point(a, b):
                debug_log(a + b)
                debug_log(a * b)
            case Circle(center=Point(x=Num(a), y=Num(b)), radius=Num(r)):
                debug_log(a)
                debug_log(b)
                debug_log(r)
            case Rectangle(
                top_left=Point(x=Num(a), y=Num(b)),
                bottom_right=Point(x=Num(c), y=Num(d)),
            ):
                debug_log(a + c)
                debug_log(b + d)
            case Color.RED | Color.BLUE:
                debug_log(x)
            case Num(n):
                debug_log(n * 2)
            case (Num(a), Num(b), Num(c)):
                debug_log(a + b + c)
            case _:
                debug_log(-1)
        debug_log(123)

    def fn():
        m(p1)
        m(c)
        m(r)
        m(color)
        m(num)
        m(tpl)
        m(text)

    run_and_validate(fn)


def test_match_nested_conditions():
    p = Point(5, 5)
    c = Circle(Point(0, 0), 15)
    r = Rectangle(Point(0, 0), Point(20, 20))
    n = 100
    s = "hello"

    def m(x):
        match x:
            case Circle(center=Point(x=Num(a), y=Num(b)), radius=Num(r)) if r > 10:
                debug_log(a)
                debug_log(b)
                debug_log(r)
            case Rectangle(
                top_left=Point(x=Num(a), y=Num(b)),
                bottom_right=Point(x=Num(c), y=Num(d)),
            ):
                area = (c - a) * (d - b)
                debug_log(area)
            case Point(x=Num(a), y=Num(b)) if a == b:
                debug_log(a)
            case Num(n) if n > 50:
                debug_log(n)
            case _:
                debug_log(-2)
        debug_log(123)

    def fn():
        m(c)
        m(r)
        m(p)
        m(n)
        m(s)

    run_and_validate(fn)


def test_match_nesting_tuples():
    t1 = (1, 2, 3)
    t2 = ((5, 6), 7, 8)
    t3 = ((10, 11), 7, 8)
    t4 = (1, 2, 3, 4)
    t5 = (1, 2)
    t6 = (1, 2, 3, 4, 5)
    t7 = (4, 3, 2, 1)

    def m(x):
        match x:
            case (Num() as a, b, c):
                debug_log(a)
                debug_log(b)
                debug_log(c)
            case ((a, b) as x, c, d) as y:
                debug_log(a)
                debug_log(b)
                debug_log(c)
                debug_log(d)
                for i in x:
                    for j in y:
                        if isinstance(j, tuple):
                            for k in j:
                                debug_log(k)
                        else:
                            debug_log(i + j)
            case (10, 11, 7, 8):
                debug_log(10)
            case (a, b):
                debug_log(a)
                debug_log(b)
            case (a, b, c, d):
                debug_log(a)
                debug_log(b)
                debug_log(c)
                debug_log(d)
            case _:
                debug_log(-1)
        debug_log(123)

    def fn():
        m(t1)
        m(t2)
        m(t3)
        m(t4)
        m(t5)
        m(t6)
        m(t7)

    run_and_validate(fn)


def test_match_nest_arrays():
    a1 = Array(1, 2, 3)
    a2 = Array(Array(1, 2), Array(3, 4), Array(5, 6))
    a3 = Array(1, 2, 4)
    a4 = Array(1, 2, 3, 4)
    a5 = Array(1, 2)
    a6 = Array(1, 2, 3, 4, 5)
    a7 = Array(Array(4, 3), Array(2, 1))
    a8 = Array(Array(Array(123)))
    a9 = Array(Array(Array(Array(123))))
    a10 = Array(Array(Array(Array(456))))
    a11 = 1

    def m(x):
        match x:
            case (Num() as a, b, c):
                debug_log(a)
                debug_log(b)
                debug_log(c)
            case ((a, b) as x, (c, d) as y):
                debug_log(a)
                debug_log(b)
                debug_log(c)
                debug_log(d)
                for i in x:
                    for j in y:
                        if isinstance(j, Array):
                            for k in j:
                                debug_log(k)
                        else:
                            debug_log(i + j)
            case (1, 2, 3):
                debug_log(1)
            case (a, b) if isinstance(a, Num):
                debug_log(a)
                debug_log(b)
            case (a, b, c, d):
                debug_log(a)
                debug_log(b)
                debug_log(c)
                debug_log(d)
            case ((((123,),),),):
                debug_log(111)
            case ((((v,),),),):
                debug_log(v)
            case Array() as a:
                for i in a:
                    if isinstance(i, Num):
                        debug_log(10 * i)
                    elif isinstance(i, Array):
                        for j in i:
                            if isinstance(j, Num):
                                debug_log(9 * j)
        debug_log(123)

    def fn():
        m(a1)
        m(a2)
        m(a3)
        m(a4)
        m(a5)
        m(a6)
        m(a7)
        m(a8)
        m(a9)
        m(a10)
        m(a11)

    run_and_validate(fn)


def test_match_nest_var_arrays():
    def m(x):
        match x:
            case (Num() as a, b, c):
                debug_log(a)
                debug_log(b)
                debug_log(c)
            case ((a, b) as x, (c, d) as y):
                debug_log(a)
                debug_log(b)
                debug_log(c)
                debug_log(d)
                for i in x:
                    for j in y:
                        if isinstance(j, Array):
                            for k in j:
                                debug_log(k)
                        else:
                            debug_log(i + j)
            case (1, 2, 3):
                debug_log(1)
            case (a, b) if isinstance(a, Num):
                debug_log(a)
                debug_log(b)
            case (a, b, c, d) if isinstance(a, Num):
                debug_log(a)
                debug_log(b)
                debug_log(c)
                debug_log(d)
            case ((((123,),),),):
                debug_log(111)
            case ((((v,),),),):
                debug_log(v)
            case Array() as a:
                for i in a:
                    if isinstance(i, Num):
                        debug_log(10 * i)
                    elif isinstance(i, Array):
                        for j in i:
                            if isinstance(j, Num):
                                debug_log(9 * j)

    def fn():
        a1 = VarArray[Num, 10].new()
        a1.append(1)
        m(a1)
        a1.append(2)
        m(a1)
        a1.append(3)
        m(a1)
        a1.append(4)
        m(a1)
        a1.append(5)
        m(a1)
        a1.clear()
        m(a1)

        a2 = VarArray[VarArray[Num, 2], 3].new()
        a2_0 = VarArray[Num, 2].new()
        a2_0.append(1)
        a2_0.append(2)
        a2.append(a2_0)
        m(a2)
        a2_1 = VarArray[Num, 2].new()
        a2_1.append(3)
        a2_1.append(4)
        a2.append(a2_1)
        m(a2)
        a2_2 = VarArray[Num, 2].new()
        a2_2.append(5)
        a2_2.append(6)
        a2.append(a2_2)
        m(a2)

        a3 = VarArray[Array[Array[Array[int, 1], 1], 1], 1].new()
        m(a3)
        a3_0 = Array(Array(Array(1)))
        a3.append(a3_0)
        m(a3)
        a3.clear()
        m(a3)

    run_and_validate(fn)


def test_match_nested_shape_records():
    circle = Shape(kind="circle", data=Circle(center=Point(0, 0), radius=10))
    rectangle = Shape(
        kind="rectangle",
        data=Rectangle(top_left=Point(0, 0), bottom_right=Point(20, 10)),
    )
    unknown = Shape(kind="unknown", data=None)
    num = 5

    def m(x):
        match x:
            case Shape(
                kind="circle",
                data=Circle(center=Point(x=Num(a), y=Num(b)), radius=Num(r)),
            ):
                debug_log(a)
                debug_log(b)
                debug_log(r)
            case Shape(
                kind="rectangle",
                data=Rectangle(
                    top_left=Point(x=Num(a), y=Num(b)),
                    bottom_right=Point(x=Num(c), y=Num(d)),
                ),
            ):
                debug_log((c - a) * (d - b))
            case Shape(kind=_, data=None):
                debug_log(-3)
            case Num(n):
                debug_log(n * n)
            case _:
                debug_log(-4)
        debug_log(123)

    def fn():
        m(circle)
        m(rectangle)
        m(unknown)
        m(num)
        m("test")

    run_and_validate(fn)


def test_match_enum_patterns():
    color1 = Color.RED
    color2 = Color.GREEN
    color3 = Color.BLUE
    num = 7
    text = "color"

    def m(x):
        match x:
            case Num(Color.RED | Color.BLUE):
                debug_log(x)
            case Color.GREEN:
                debug_log(100)
            case Num(n):
                debug_log(n + 10)
        debug_log(123)

    def fn():
        m(color1)
        m(color2)
        m(color3)
        m(num)
        m(text)

    run_and_validate(fn)


def test_match_nested_shapes_and_pair():
    p1 = Point(1, 1)
    p2 = Point(2, 2)
    p3 = Point(3, 3)
    c = Circle(center=p1, radius=10)
    r = Rectangle(top_left=p2, bottom_right=p3)
    shape_circle = Shape(kind="circle", data=c)
    shape_rectangle = Shape(kind="rectangle", data=r)
    pair = Pair(first=p1, second=r)

    def m(x):
        match x:
            case Shape(
                kind="circle",
                data=Circle(center=Point(x=Num(a), y=Num(b)), radius=Num(r)),
            ) if r > 5:
                debug_log(a + b)
                debug_log(r)
            case Shape(
                kind="rectangle",
                data=Rectangle(
                    top_left=Point(x=Num(a), y=Num(b)),
                    bottom_right=Point(x=Num(c), y=Num(d)),
                ),
            ) if (c - a) > 0 and (d - b) > 0:
                debug_log((c - a) * (d - b))
            case Pair(first=Point(x=Num(a), y=Num(b)) as p, second=_):
                debug_log(p.x + p.y)
                debug_log(a * b)
            case Point(x=Num(a), y=Num(b)) if a == b:
                debug_log(a * b)
            case _:
                pass
        debug_log(123)

    def fn():
        m(shape_circle)
        m(shape_rectangle)
        m(pair)
        m(p1)
        m(p2)
        m(p3)

    run_and_validate(fn)


def test_match_partial_binding():
    p1 = Pair(1, 2)
    p2 = Pair2(3, 4)
    p3 = Pair(5, 6)
    p4 = Pair2(7, 8)
    p5 = Pair(3, 2)
    p6 = Pair2(1, 3)
    p7 = Pair(Pair(1, 2), Pair(3, 4))
    p8 = Pair2(Pair2(1, 2), Pair2(3, 4))
    p9 = Pair3(1, 2)

    def m(x):
        match x:
            case Pair(1, _) | Pair2(_, _) if isinstance(x, Pair3):
                # This is unreachable, but it's a valid pattern we're testing
                debug_log(123)
            case Pair(1, b) | Pair2(2, b):
                debug_log(b)
            case Pair(a, 2) | Pair2(a, 3):
                debug_log(a)
            case Pair(Num(a), b) | Pair2(a, Num(b)) if a > 3:
                debug_log(a + b)
            case Pair(a, b) | Pair2(a, b) if isinstance(a, Num) and a <= 3:
                debug_log(a - b)
            case Pair(Pair() as a) | Pair2(7, a):
                if isinstance(a, Pair):
                    debug_log(a.first)
                    debug_log(a.second)
                else:
                    debug_log(a)
            case _:
                debug_log(-1)

    def fn():
        m(p1)
        m(p2)
        m(p3)
        m(p4)
        m(p5)
        m(p6)
        m(p7)
        m(p8)
        m(p9)

    run_and_validate(fn)


def test_match_class_mixed_positional_and_keyword():
    def fn():
        p = Point(0, 5)
        match p:
            case Point(0, y=1):
                return 1
            case _:
                return 0

    assert run_and_validate(fn) == 0


def test_match_class_mixed_positional_and_keyword_true_case():
    def fn():
        p = Point(0, 5)
        match p:
            case Point(0, y=5):
                return 1
            case _:
                return 0

    assert run_and_validate(fn) == 1


def test_match_class_duplicate_positional_and_keyword_attr_rejected():
    def fn():
        p = Point(0, 5)
        match p:
            case Point(0, x=1):
                return 1
            case _:
                return 0

    with pytest.raises(CompilationError, match="multiple sub-patterns for attribute"):
        run_compiled(fn)


def test_match_capture_is_loop_carried_in_while():
    def fn():
        total = 0
        x = 0
        i = 0
        while i < 4:
            total = total * 10 + x
            debug_log(x)
            match i:
                case x:
                    pass
            i += 1
        return total

    assert run_and_validate(fn) == 12


def test_match_capture_is_loop_carried_in_for():
    def fn():
        total = 0
        x = 0
        for i in range(4):
            total = total * 10 + x
            debug_log(x)
            match i:
                case x:
                    pass
        return total

    assert run_and_validate(fn) == 12


def test_match_as_capture_is_loop_carried():
    def fn():
        total = 0
        z = 0
        i = 0
        while i < 3:
            total = total * 10 + z
            match i:
                case 0 | 1 | 2 as z:
                    pass
            i += 1
        return total

    assert run_and_validate(fn) == 1


def test_match_capture_nested_under_as_is_loop_carried():
    def fn():
        total = 0
        b = 0
        i = 0
        while i < 4:
            total = total * 10 + b
            debug_log(b)
            match (i, i + 1):
                case (_, b) as whole:  # noqa: F841
                    pass
            i += 1
        return total

    assert run_and_validate(fn) == 123


def test_match_dict_subject_does_not_match_sequence_pattern():
    def fn():
        d = {0: 10, 1: 20}
        match d:
            case [10, 20]:
                return 100
            case _:
                return 200

    assert run_and_validate(fn) == 200


def test_match_dict_subject_sequence_capture_falls_through():
    def fn():
        d = {0: 10, 1: 20}
        match d:
            case [a, b]:
                return a * 1000 + b
            case _:
                return -1

    assert run_and_validate(fn) == -1


def test_match_dict_subject_with_non_zero_keys_falls_through():
    def fn():
        d = {1: 10, 2: 20}
        match d:
            case [a, b]:
                return a * 1000 + b
            case _:
                return 2

    assert run_and_validate(fn) == 2


def test_match_dict_nested_in_tuple_does_not_match_sequence_pattern():
    def fn():
        d = {0: 5}
        match (d,):
            case [[5]]:
                return 1
            case _:
                return 2

    assert run_and_validate(fn) == 2


def test_match_enum_class_subject_does_not_match_sequence_pattern():
    def fn():
        match Color:
            case [_, _, _]:
                return 1
            case _:
                return 2

    assert run_and_validate(fn) == 2


def test_match_empty_list_pattern_against_empty_tuple():
    def fn():
        t = ()
        match t:
            case []:
                return 1
            case _:
                return 2

    assert run_and_validate(fn) == 1


def test_match_empty_tuple_pattern_against_empty_tuple():
    def fn():
        t = ()
        match t:
            case ():
                return 10
            case _:
                return 20

    assert run_and_validate(fn) == 10


def test_match_empty_sequence_pattern_against_empty_array():
    def fn():
        a = Array[Num, 0]()
        match a:
            case []:
                return 1
            case _:
                return 2

    assert run_and_validate(fn) == 1


def test_match_empty_sequence_pattern_with_guard():
    def fn():
        t = ()
        x = 0
        match t:
            case [] if x > 5:
                return 1
            case []:
                return 2
            case _:
                return 3

    assert run_and_validate(fn) == 2


def test_match_empty_sequence_pattern_against_non_empty_tuple():
    def fn():
        t = (1, 2)
        match t:
            case []:
                return 1
            case _:
                return 2

    assert run_and_validate(fn) == 2


def test_match_sequence_pattern_with_trailing_star_rejected():
    def fn():
        t = (1, 2, 3)
        match t:
            case [a, *rest]:  # noqa: F841
                return a
            case _:
                return -1

    with pytest.raises(CompilationError, match="Star sub-patterns"):
        run_compiled(fn)


def test_match_sequence_pattern_with_star_in_middle_rejected():
    def fn():
        t = (1, 2, 3)
        match t:
            case [a, *mid, b]:  # noqa: F841
                return a + b
            case _:
                return -1

    with pytest.raises(CompilationError, match="Star sub-patterns"):
        run_compiled(fn)


def test_match_sequence_pattern_with_only_star_rejected():
    def fn():
        t = (1, 2, 3)
        match t:
            case [*rest]:
                return len(rest)
            case _:
                return -1

    with pytest.raises(CompilationError, match="Star sub-patterns"):
        run_compiled(fn)


def test_match_sequence_pattern_with_anonymous_star_rejected():
    def fn():
        t = (1, 2, 3)
        match t:
            case [1, *_]:
                return 1
            case _:
                return -1

    with pytest.raises(CompilationError, match="Star sub-patterns"):
        run_compiled(fn)


def test_match_nested_sequence_pattern_with_star_rejected():
    def fn():
        t = (1, 2)
        match t:
            case [[x, *rest]]:  # noqa: F841
                return x
            case _:
                return -1

    with pytest.raises(CompilationError, match="Star sub-patterns"):
        run_compiled(fn)


def test_match_sequence_pattern_with_star_on_non_sequence_subject_rejected():
    def fn():
        d = {0: 10, 1: 20}
        match d:
            case [a, *rest]:  # noqa: F841
                return a
            case _:
                return -1

    with pytest.raises(CompilationError, match="Star sub-patterns"):
        run_compiled(fn)


def test_match_sequence_pattern_without_star_still_matches():
    def fn():
        t = (1, 2, 3)
        match t:
            case [a, b, c]:
                return a * 100 + b * 10 + c
            case _:
                return -1

    assert run_and_validate(fn) == 123


def test_match_sequence_pattern_without_star_length_mismatch_falls_through():
    def fn():
        t = (1, 2, 3)
        match t:
            case [a, b]:
                return a * 10 + b
            case [a, b, c]:
                return a * 100 + b * 10 + c
            case _:
                return -1

    assert run_and_validate(fn) == 123


def test_match_nested_sequence_pattern_without_star_still_matches():
    def fn():
        t = ((1, 2), 3)
        match t:
            case [[a, b], c]:
                return a * 100 + b * 10 + c
            case _:
                return -1

    assert run_and_validate(fn) == 123


class WeirdEq(Record):
    value: Num

    def __eq__(self, other):
        # __eq__ returns a VarArray rather than a Num: empty when the values differ, one element when they
        # match. A value pattern truth-tests the result of ==, per PEP 634, so an empty result is a non-match.
        result = VarArray[Num, 1].new()
        if self.value == other.value:
            result.append(1)
        return result

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        raise TypeError("unhashable type: 'WeirdEq'")


class MatchValueConstants:
    # Value patterns in a case must be dotted names, so this class holds the constants they name.
    TUPLE_A = (1, 2)
    TUPLE_B = (3, 4)
    NESTED = ((1, 2), 3)
    ARRAY = Array(1, 2)
    RANGE = Range(0, 3)
    WEIRD = WeirdEq(0)
    WEIRD_OTHER = WeirdEq(7)


def test_match_runtime_tuple_against_constant_tuple_pattern():
    def fn():
        t = (black_box_value(1), 2)
        match t:
            case MatchValueConstants.TUPLE_B:
                return 20
            case MatchValueConstants.TUPLE_A:
                return 10
            case _:
                return -1

    assert run_and_validate(fn) == 10


def test_match_runtime_tuple_against_constant_tuple_pattern_no_match():
    def fn():
        t = (black_box_value(5), 2)
        match t:
            case MatchValueConstants.TUPLE_A:
                return 10
            case _:
                return -1

    assert run_and_validate(fn) == -1


def test_match_runtime_array_against_constant_array_pattern():
    def fn():
        a = Array(black_box_value(1), 2)
        match a:
            case MatchValueConstants.ARRAY:
                return 10
            case _:
                return -1

    assert run_and_validate(fn) == 10


def test_match_runtime_range_against_constant_range_pattern():
    def fn():
        r = Range(0, black_box_value(3))
        match r:
            case MatchValueConstants.RANGE:
                return 10
            case _:
                return -1

    assert run_and_validate(fn) == 10


def test_match_var_array_against_constant_array_pattern():
    def fn():
        v = VarArray[Num, 4].new()
        v.append(black_box_value(1))
        v.append(2)
        match v:
            case MatchValueConstants.ARRAY:
                return 10
            case _:
                return -1

    assert run_and_validate(fn) == 10


def test_match_runtime_tuple_against_or_pattern_of_constant_tuples():
    def fn():
        t = (black_box_value(3), 4)
        match t:
            case MatchValueConstants.TUPLE_A | MatchValueConstants.TUPLE_B:
                return 10
            case _:
                return -1

    assert run_and_validate(fn) == 10


def test_match_runtime_tuple_with_guard_after_runtime_value_pattern():
    def fn():
        n = black_box_value(1)
        t = (n, 2)
        match t:
            case MatchValueConstants.TUPLE_A if n > 5:
                return 1
            case MatchValueConstants.TUPLE_A:
                return 2
            case _:
                return -1

    assert run_and_validate(fn) == 2


def test_match_runtime_tuple_with_as_capture():
    def fn():
        t = (black_box_value(1), 2)
        match t:
            case MatchValueConstants.TUPLE_A as m:
                return m[0] + m[1]
            case _:
                return -1

    assert run_and_validate(fn) == 3


def test_match_runtime_value_pattern_nested_in_sequence_pattern():
    def fn():
        t = ((black_box_value(1), 2), 3)
        match t:
            case [MatchValueConstants.TUPLE_A, 3]:
                return 10
            case _:
                return -1

    assert run_and_validate(fn) == 10


def test_match_runtime_nested_tuple_against_constant_nested_tuple_pattern():
    def fn():
        t = ((black_box_value(1), 2), 3)
        match t:
            case MatchValueConstants.NESTED:
                return 10
            case _:
                return -1

    assert run_and_validate(fn) == 10


def test_match_runtime_tuple_pattern_in_loop():
    def fn():
        total = 0
        i = 0
        while i < 4:
            t = (black_box_value(i), 2)
            match t:
                case MatchValueConstants.TUPLE_A:
                    total += 10
                case _:
                    total += 1
            i += 1
        return total

    assert run_and_validate(fn) == 13


def test_match_num_subject_against_constant_tuple_pattern_falls_through():
    def fn():
        n = black_box_value(1)
        match n:
            case MatchValueConstants.TUPLE_A:
                return 10
            case _:
                return -1

    assert run_and_validate(fn) == -1


def test_match_value_pattern_with_non_num_truthy_eq_matches():
    def fn():
        w = WeirdEq(black_box_value(0))
        match w:
            case MatchValueConstants.WEIRD:
                return 10
            case _:
                return -1

    assert run_and_validate(fn) == 10


def test_match_value_pattern_with_non_num_falsy_eq_does_not_match():
    def fn():
        w = WeirdEq(black_box_value(0))
        match w:
            case MatchValueConstants.WEIRD_OTHER:
                return 10
            case _:
                return -1

    assert run_and_validate(fn) == -1


class TerminatingEq(Record):
    value: Num

    def __eq__(self, other):
        assert_true(False, "eq says no")
        return 1

    def __hash__(self):
        raise TypeError("unhashable type: 'TerminatingEq'")


class TerminatingEqConstants:
    ONLY = TerminatingEq(0)


def test_match_value_pattern_with_terminating_eq_terminates():
    # The match must still compile when a traced __eq__ terminates the callback and leaves no result to test.
    def fn():
        w = TerminatingEq(0)
        match w:
            case TerminatingEqConstants.ONLY:
                return 10
            case _:
                return -1

    with pytest.raises(AssertionError, match="eq says no"):
        run_and_validate(fn)


class Unrelated(Record):
    n: Num


class PropertyHolder(Record):
    a: Num
    b: Num

    @property
    def p(self) -> Num:
        return self.b


def test_match_class_pattern_reads_no_property_after_a_statically_failing_sub_pattern():
    # `a=Unrelated()` cannot match a Num, so it leaves the arm's context dead. The keyword loop has to stop
    # there, as the sequence and or-pattern loops do: `p` is a property, so reading it would trace its
    # getter from that dead context and report a terminating call this program does not contain.
    def fn():
        h = PropertyHolder(1, 2)
        match h:
            case PropertyHolder(a=Unrelated(), p=1):
                return 10
            case _:
                return -1

    assert run_and_validate(fn) == -1


def test_match_capture_does_not_leak_from_failed_sequence_pattern():
    # CPython applies a pattern's captures only once the whole pattern has matched, so a case that
    # binds and then fails leaves the name at its pre-match value.
    def fn():
        a = 99
        match Array(1, 2):
            case [a, 3]:
                pass
            case _:
                pass
        return a

    assert run_and_validate(fn) == 99


def test_match_capture_does_not_leak_from_failed_runtime_sub_pattern():
    # The failing sub-pattern is a real runtime branch here, not a statically folded one.
    def fn():
        a = 99
        arr = Array(7, black_box_value(4))
        match arr:
            case [a, 3]:
                pass
            case _:
                pass
        return a

    assert run_and_validate(fn) == 99


def test_match_capture_does_not_leak_from_failed_class_keyword_pattern():
    def fn():
        a = 99
        match Point(1, 2):
            case Point(x=a, y=3):
                pass
            case _:
                pass
        return a

    assert run_and_validate(fn) == 99


def test_match_capture_does_not_leak_from_failed_class_positional_pattern():
    def fn():
        a = 99
        match Point(1, 2):
            case Point(a, 3):
                pass
            case _:
                pass
        return a

    assert run_and_validate(fn) == 99


def test_match_as_capture_does_not_leak_from_failed_pattern():
    def fn():
        a = 99
        match Array(1, 2):
            case [1 as a, 3]:
                pass
            case _:
                pass
        return a

    assert run_and_validate(fn) == 99


def test_match_nested_sequence_capture_does_not_leak_from_failed_pattern():
    def fn():
        a = 99
        arr = Array(Array(1, 2), Array(3, 4))
        match arr:
            case [[a, 5], _]:
                pass
            case _:
                pass
        return a

    assert run_and_validate(fn) == 99


def test_match_leaked_capture_does_not_select_a_later_arm():
    # The consequence of a leak: the second case's guard reads `a`, so a stale binding runs a
    # different arm body.
    def fn():
        a = 0
        match Array(1, 2):
            case [a, 3]:
                debug_log(1)
            case _ if a == 1:
                debug_log(2)
            case _:
                debug_log(3)
        return a

    assert run_and_validate(fn) == 0


def test_match_capture_under_or_does_not_leak_from_failed_pattern():
    # The capture is on the MatchAs above the or-pattern.
    def fn():
        y = 99
        match Array(1, 2):
            case [(1 | 2) as y, 3]:
                pass
            case _:
                pass
        return y

    assert run_and_validate(fn) == 99


def test_match_capture_inside_or_alternative_does_not_leak_from_failed_pattern():
    # The capture is inside each alternative, so it has to survive the or-pattern's merge and still
    # not be visible on the path where the trailing literal fails.
    def fn():
        y = 99
        match Array(1, 2):
            case [(1 as y) | (2 as y), 3]:
                pass
            case _:
                pass
        return y

    assert run_and_validate(fn) == 99


def test_match_top_level_or_capture_does_not_leak_from_failed_pattern():
    def fn():
        y = 99
        match Array(1, 2):
            case [y, 5] | [y, 3]:
                pass
            case _:
                pass
        return y

    assert run_and_validate(fn) == 99


def test_match_capture_does_not_leak_from_failed_class_sub_pattern():
    # The sub-pattern that fails is a static class check rather than a value test.
    def fn():
        y = 99
        match Array(1, 2):
            case [y, Array()]:
                pass
            case _:
                pass
        return y

    assert run_and_validate(fn) == 99


def test_match_or_capture_inside_loop_does_not_leak():
    # An or-pattern's captures are merged through temporary bindings, which must not survive into a
    # loop's back edge.
    def fn():
        total = 0
        y = 0
        i = 0
        while i < 4:
            total = total * 10 + y
            match Array(i, 2):
                case [(1 as y) | (2 as y), 9]:
                    pass
                case _:
                    pass
            i += 1
        return total

    assert run_and_validate(fn) == 0


def test_match_or_capture_inside_loop_is_kept_when_the_pattern_matches():
    def fn():
        total = 0
        y = 0
        i = 0
        while i < 4:
            total = total * 10 + y
            match Array(i, 2):
                case [(1 as y) | (2 as y), 2]:
                    pass
                case _:
                    pass
            i += 1
        return total

    assert run_and_validate(fn) == 12


def test_match_capture_is_kept_when_the_guard_fails():
    # A failing guard is the one place CPython does keep the captures.
    def fn():
        a = 99
        match Array(1, 2):
            case [a, b] if b == 5:
                pass
            case _:
                pass
        return a

    assert run_and_validate(fn) == 1


def test_match_capture_at_the_end_of_a_failed_or_alternative_does_not_leak():
    def fn():
        a = 99
        match Array(7, 2):
            case [1, a] | [2, a]:
                pass
            case _:
                pass
        return a

    assert run_and_validate(fn) == 99


def test_match_capture_is_kept_when_the_pattern_matches():
    def fn():
        a = 99
        match Array(1, 2):
            case [a, 2]:
                pass
            case _:
                pass
        return a

    assert run_and_validate(fn) == 1


def test_match_capture_is_kept_when_a_runtime_pattern_matches():
    def fn():
        a = 99
        arr = Array(7, black_box_value(3))
        match arr:
            case [a, 3]:
                debug_log(1)
            case _:
                debug_log(2)
        return a

    assert run_and_validate(fn) == 7


def test_match_capture_of_a_different_type_conflicts_with_the_pre_match_binding():
    # The capture no longer reaches the not-matching path, but the matching path is still traced, so `a`
    # holds a record on one and a Num on the other. That is the subset's single-live-definition rule
    # rather than a leak, and the read after the match is where it surfaces.
    def fn():
        a = Point(1, 2)
        match Array(1, 2):
            case [a, 3]:
                pass
            case _:
                pass
        return a.x

    with pytest.raises(CompilationError, match="Binding 'a' has multiple conflicting definitions"):
        run_compiled(fn)


def test_match_capture_that_is_the_only_binding_is_not_defined_after_a_failed_pattern():
    # Python raises UnboundLocalError here. Deferring the capture leaves the name unbound on the
    # not-matching path, which the compiler reports at the read.
    def fn():
        match Array(1, 2):
            case [a, 5, 6]:
                pass
            case _:
                pass
        return a

    with pytest.raises(CompilationError, match="Name a is not defined"):
        run_compiled(fn)


def test_match_capture_that_is_the_only_binding_is_not_guaranteed_after_a_runtime_pattern():
    def fn():
        match Array(1, 2):
            case [a, 3]:
                pass
            case _:
                pass
        return a

    with pytest.raises(CompilationError, match="Binding 'a' has multiple conflicting definitions"):
        run_compiled(fn)


def test_match_capture_of_a_record_does_not_leak_into_a_num_binding():
    def fn():
        a = 1
        match Array(Point(4, 5), Point(6, 7)):
            case [a, 3]:
                pass
            case _:
                pass
        return a + 1

    assert run_and_validate(fn) == 2


def test_match_or_capture_of_conflicting_records_reports_the_capture_name():
    # The alternatives bind different objects of a reference type, which cannot merge. The error has to
    # name the capture rather than the temporary the alternatives were merged through.
    def fn():
        match Array(Point(0, 1), Point(1, 2)):
            case [Point(0, _) as p, _] | [_, Point(1, _) as p]:
                return p.y
            case _:
                return -1

    with pytest.raises(CompilationError, match="Binding 'p' has multiple conflicting definitions"):
        run_compiled(fn)


def test_match_or_capture_of_conflicting_records_still_compiles_when_unread():
    def fn():
        match Array(Point(0, 1), Point(1, 2)):
            case [Point(0, _) as p, _] | [_, Point(1, _) as p]:  # noqa: F841
                return 1
            case _:
                return -1

    assert run_and_validate(fn) == 1
