from sonolus.script.record import Record
from tests.script.conftest import run_compiled


class _PropBox(Record):
    value: int

    @property
    def prop(self):
        return 111


def test_record_property_replaced_between_compiles():
    def fn():
        return _PropBox(0).prop

    assert run_compiled(fn) == 111

    original = _PropBox.prop
    _PropBox.prop = property(lambda self: 222)
    try:
        assert run_compiled(fn) == 222
    finally:
        _PropBox.prop = original


def test_fn_defaults_rebound_between_compiles():
    def make():
        def inner(x=10):
            return x

        return inner

    fn = make()
    assert run_compiled(fn) == 10

    fn.__defaults__ = (20,)
    assert run_compiled(fn) == 20
