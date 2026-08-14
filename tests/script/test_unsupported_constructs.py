"""Tests that each Python construct outside the compiled subset is rejected with its own message.

Every snippet is a real module-level def rather than a string compiled at test time, because the visitor reads
a function's source through inspect.getsource. run_compiled is what runs them: the point of each snippet is
that it never reaches the interpreter, and several are not meaningful as plain Python either, so there is no
result for run_and_validate to compare against.

The rows after the construct rows reject an operand rather than a construct. A `for` loop and a generator
expression each carry their own copy of the two iterable guards, so a copy dropped from one of them would
otherwise still be covered by the other; the item-access and membership rows pin the wording Python uses for
the same mistake.

The pattern is the full message, so a snippet that starts failing for an unrelated reason fails the test rather
than passing on a different guard. Four messages are genuinely shared by two guards each, and there the snippet
is what discriminates: `import x` and `from x import y` both report "Import statements are not supported", an
f-string is rejected by whichever of the two f-string guards the visitor reaches first, and the two iterable
messages are each raised once for a `for` loop and once for a generator expression. The same applies to
`case int():`, whose message is also raised for `isinstance(x, int)` from a different module.
"""

import re

import pytest

from sonolus.script.internal.error import CompilationError
from sonolus.script.record import Record
from sonolus.script.vec import Vec2
from tests.script.conftest import run_compiled

_module_global = 0

# A name for the `with` snippet to bind, so that it is valid Python. The visitor rejects the statement before
# looking at the expression, so the value never matters.
_context = None


def s_async_def():
    async def g():  # ruff: ignore[unused-async]
        return 1

    return 1


def s_class_def():
    class C:
        pass

    return 1


def s_del_name():
    x = 1
    del x
    return 2


def s_del_attribute():
    v = Vec2(1, 2)
    del v.x
    return 2


def s_del_tuple():
    x = 1
    y = 2
    del (x, y)
    return 2


def s_type_alias():
    type Alias = int  # ruff: ignore[unused-variable]
    return 1


def s_with():
    with _context:
        pass
    return 1


def s_raise():
    raise ValueError("x")


def s_try():
    try:
        x = 1
    except ValueError:
        x = 2
    return x


def s_try_star():
    try:
        x = 1
    except* ValueError:
        x = 2
    return x


def s_import():
    import math

    return math.floor(1.5)


def s_import_from():
    from math import floor

    return floor(1.5)


def s_global():
    global _module_global  # ruff: ignore[global-statement]
    _module_global = 1
    return 1


def s_nonlocal():
    x = 1

    def g():
        nonlocal x
        x = 2

    g()
    return x


def s_set_comprehension():
    return len({x for x in range(3)})  # ruff: ignore[unnecessary-comprehension]


def s_dict_comprehension():
    return len({x: x for x in range(3)})


def s_fstring():
    x = 1
    return len(f"{x}")


def s_list_literal():
    a = [1, 2, 3]
    return len(a)


def s_slice():
    t = (1, 2, 3)
    return t[0:2]


def s_starred_assignment():
    a, *b = (1, 2)  # ruff: ignore[unused-variable]
    return a


def s_match_true():
    x = 1
    match x:
        case True:
            return 0
        case _:
            return 1


def s_match_false():
    x = 1
    match x:
        case False:
            return 0
        case _:
            return 1


def s_match_class_int():
    x = 1
    match x:
        case int():
            return 0
        case _:
            return 1


def s_match_mapping():
    x = 1
    match x:
        case {"a": v}:  # ruff: ignore[unused-variable]
            return 0
        case _:
            return 1


class _NotAnIterator(Record):
    """A record whose __iter__ hands back something that is not a SonolusIterator."""

    x: int

    def __iter__(self):
        return self.x


def s_for_over_non_iterable():
    total = 0
    for v in 5.0:
        total += v
    return total


def s_genexpr_over_non_iterable():
    return sum(v for v in 5.0)


def s_for_over_bad_iterator():
    total = 0
    for v in _NotAnIterator(1):
        total += v
    return total


def s_genexpr_over_bad_iterator():
    return sum(v for v in _NotAnIterator(1))


def s_subscript_non_subscriptable():
    x = 1.0
    return x[0]


def s_item_assignment_unsupported():
    x = 1.0
    x[0] = 2.0
    return x


def s_item_deletion_unsupported():
    x = 1.0
    del x[0]
    return x


def s_in_non_container():
    return 1.0 in 2.0  # ruff: ignore[comparison-of-constant]


def s_not_in_non_container():
    return 1.0 not in 2.0  # ruff: ignore[comparison-of-constant]


def s_walrus_in_genexpr():
    y = 0
    return sum((y := v) for v in (1, 2)) * 100 + y


def s_walrus_in_genexpr_filter_clause():
    y = 0
    return sum(v for v in (1, 2) if (y := v) > 1) * 100 + y


def s_walrus_in_a_genexpr_inside_a_lambda():
    # The rejection is keyed off the visitor that traces the walrus, so a generator expression inside a
    # lambda is rejected too: the genexpr's own visitor is still the one that reaches it. The mirror shape,
    # a lambda inside a generator expression, keeps compiling and is pinned in test_flow.py.
    y = 0
    f = lambda: sum((y := v) for v in (1, 2))  # ruff: ignore[lambda-assignment, unused-variable]
    return f() * 100 + y


_WALRUS_IN_GENEXPR = "Assignment expressions (`:=`) in a generator expression are not supported."

UNSUPPORTED = [
    (s_async_def, "Async functions are not supported"),
    (s_class_def, "Classes within functions are not supported"),
    (s_del_name, "Deleting variables is not supported"),
    (s_del_attribute, "Deleting attributes is not supported"),
    (s_del_tuple, "Unsupported delete target"),
    (s_type_alias, "Type aliases are not supported"),
    (s_with, "With statements are not supported"),
    (s_raise, "Raise statements are not supported"),
    (s_try, "Try statements are not supported"),
    (s_try_star, "Try* statements are not supported"),
    (s_import, "Import statements are not supported"),
    (s_import_from, "Import statements are not supported"),
    (s_global, "Global statements are not supported"),
    (s_nonlocal, "Nonlocal statements are not supported"),
    (s_set_comprehension, "Set comprehensions are not supported"),
    (s_dict_comprehension, "Dict comprehensions are not supported"),
    (s_fstring, "F-strings are not supported"),
    (s_list_literal, "List literals are not supported"),
    (s_slice, "Slices are not supported"),
    (s_starred_assignment, "Starred assignment is not supported"),
    (s_match_true, "Matching against True is not supported, use 1 instead"),
    (s_match_false, "Matching against False is not supported, use 0 instead"),
    (s_match_class_int, "Instance check against int, float, or bool is not supported, use Num instead"),
    (s_match_mapping, "Match mappings are not supported"),
    (s_walrus_in_genexpr, _WALRUS_IN_GENEXPR),
    (s_walrus_in_genexpr_filter_clause, _WALRUS_IN_GENEXPR),
    (s_walrus_in_a_genexpr_inside_a_lambda, _WALRUS_IN_GENEXPR),
    (s_for_over_non_iterable, "'Num' object is not iterable"),
    (s_genexpr_over_non_iterable, "'Num' object is not iterable"),
    (s_for_over_bad_iterator, "iter() returned non-iterator of type 'Num'"),
    (s_genexpr_over_bad_iterator, "iter() returned non-iterator of type 'Num'"),
    (s_subscript_non_subscriptable, "'Num' object is not subscriptable"),
    (s_item_assignment_unsupported, "'Num' object does not support item assignment"),
    (s_item_deletion_unsupported, "'Num' object does not support item deletion"),
    (s_in_non_container, "argument of type 'Num' is not a container or iterable"),
    (s_not_in_non_container, "argument of type 'Num' is not a container or iterable"),
]


@pytest.mark.parametrize(
    ("snippet", "message"),
    [pytest.param(snippet, message, id=snippet.__name__.removeprefix("s_")) for snippet, message in UNSUPPORTED],
)
def test_construct_is_rejected_with_its_own_message(snippet, message):
    with pytest.raises(CompilationError, match=re.escape(message)):
        run_compiled(snippet)
