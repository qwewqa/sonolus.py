"""The wrapped-constant cache must not retain what a later compile can never look up again.

`ConstantValue._parameterized_` mints a class and a singleton per wrapped value and is keyed on the value, so
an entry for a function object or for a bound method of a per-build instance can never be hit again: a process
that compiles repeatedly, a dev-server session or a test run, accumulates them for its whole life. Dropping
entries wholesale is not the answer, because the class a wrapped value is given is its identity, and a Record
generic over a constant is parameterized on that class. Only an entry nothing holds any more may go.
"""

import gc
import weakref

from sonolus.script.internal.constant import ConstantValue
from sonolus.script.num import Num
from sonolus.script.record import Record
from tests.script.conftest import compile_fn


class _Tagged[Tag](Record):
    """A record whose type is parameterized on the class minted for its tag."""

    tag: Tag
    value: Num


def _make_caller():
    """Build a caller whose callee is a function object no other call to this can produce.

    Returns both, since the callee is the key its wrapped constant is cached under.
    """

    def callee():
        return 1.0

    def caller():
        return callee()

    return caller, callee


def test_repeated_compiles_do_not_accumulate_wrapped_constants():
    # Assert on the entries this test mints, not on len(_parameterized_): the dict is process-global and every
    # other test in the worker adds to it, so a count comparison measures whatever else the worker ran.
    refs = []
    for _ in range(3):
        caller, callee = _make_caller()
        compile_fn(caller)
        minted = ConstantValue._parameterized_.get(callee)
        assert minted is not None, "the compile wrapped no constant for the callee, so this pins nothing"
        refs.append(weakref.ref(minted))
        del caller, callee, minted
    gc.collect()
    assert [ref() for ref in refs] == [None, None, None]


def test_a_constant_still_in_use_keeps_its_class_across_compiles():
    # The class is the constant's identity: a record generic over one is parameterized on it, and a value built
    # before a compile is matched and type-checked against values built after it.
    tagged = _Tagged("tag", 1)
    caller, _ = _make_caller()
    compile_fn(caller)
    gc.collect()
    assert type(_Tagged("tag", 1)) is type(tagged)
