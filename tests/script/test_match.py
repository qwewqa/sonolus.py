"""Test cases intended to cover more complex control flow."""

from enum import IntEnum

from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from sonolus.script.debug import debug_log
from sonolus.script.num import Num
from sonolus.script.record import Record
from tests.script.conftest import run_and_validate
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
