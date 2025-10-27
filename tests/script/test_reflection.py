import pytest

from sonolus.script.array import Array
from sonolus.script.debug import debug_log
from sonolus.script.internal.error import CompilationError
from sonolus.script.record import Record
from tests.script.conftest import run_and_validate, run_compiled


class UnsupportedDescriptor:
    def __get__(self, instance, owner):
        return -1

    def __set__(self, instance, value):
        pass


class MyBox[T](Record):
    value: T

    @classmethod
    def my_classmethod(cls):
        return 123

    def my_method(self):
        return 456

    @property
    def my_property(self):
        return 789

    @my_property.setter
    def my_property(self, value):
        debug_log(value)


MyBox.unsupported_descriptor = UnsupportedDescriptor()


def test_hasattr_record_field():
    def fn():
        return hasattr(MyBox(1), "value")

    assert run_and_validate(fn) == 1


def test_hasattr_record_method():
    def fn():
        return hasattr(MyBox(1), "my_method")

    assert run_and_validate(fn) == 1


def test_hasattr_record_classmethod():
    def fn():
        return hasattr(MyBox(1), "my_classmethod")

    assert run_and_validate(fn) == 1


def test_hasattr_record_property():
    def fn():
        return hasattr(MyBox(1), "my_property")

    assert run_and_validate(fn) == 1


def test_hasattr_record_not_present():
    def fn():
        return hasattr(MyBox(1), "does_not_exist")

    assert run_and_validate(fn) == 0


def test_hasattr_record_unsupported():
    def fn():
        return hasattr(MyBox(1), "unsupported_descriptor")

    with pytest.raises(CompilationError, match="Unsupported field"):
        run_compiled(fn)


def test_hasattr_array_method():
    def fn():
        return hasattr(Array(1), "__getitem__")

    assert run_and_validate(fn) == 1


def test_hasattr_array_not_present():
    def fn():
        return hasattr(Array(1), "does_not_exist")

    assert run_and_validate(fn) == 0


def test_hasattr_type_classmethod():
    def fn():
        return hasattr(MyBox, "my_classmethod")

    assert run_and_validate(fn) == 1


def test_hasattr_type_method():
    def fn():
        return hasattr(MyBox, "my_method")

    assert run_and_validate(fn) == 1


def test_hasattr_type_not_present():
    def fn():
        return hasattr(MyBox, "does_not_exist")

    assert run_and_validate(fn) == 0


def test_getattr_record_field():
    def fn():
        box = MyBox(100)
        return box.value

    assert run_and_validate(fn) == 100


def test_getattr_record_method():
    def fn():
        box = MyBox(100)
        method = box.my_method
        return method()

    assert run_and_validate(fn) == 456


def test_getattr_record_classmethod():
    def fn():
        method = MyBox.my_classmethod
        return method()

    assert run_and_validate(fn) == 123


def test_getattr_record_property():
    def fn():
        box = MyBox(100)
        return box.my_property

    assert run_and_validate(fn) == 789


def test_getattr_record_unsupported():
    def fn():
        box = MyBox(100)
        return box.unsupported_descriptor

    with pytest.raises(CompilationError, match="Unsupported field"):
        run_compiled(fn)


def test_getattr_array_method():
    def fn():
        arr = Array(10, 20, 30)
        method = arr.__getitem__
        return method(1)

    assert run_and_validate(fn) == 20


def test_getattr_type_classmethod():
    def fn():
        method = MyBox.my_classmethod
        return method()

    assert run_and_validate(fn) == 123


def test_getattr_type_method():
    def fn():
        method = MyBox.my_method
        return method(MyBox(0))

    assert run_and_validate(fn) == 456


def test_getattr_type_not_present():
    def fn():
        return MyBox.does_not_exist

    with pytest.raises(CompilationError, match="has no attribute 'does_not_exist'"):
        run_compiled(fn)


def test_setattr_record_field():
    def fn():
        box = MyBox(0)
        box.value = 100
        return box.value

    assert run_and_validate(fn) == 100


def test_setattr_record_property():
    def fn():
        box = MyBox(0)
        box.my_property = 100
        return 1

    assert run_and_validate(fn) == 1


def test_setattr_record_unsupported():
    def fn():
        box = MyBox(0)
        box.unsupported_descriptor = 100
        return 1

    with pytest.raises(CompilationError, match="Unsupported field"):
        run_compiled(fn)


def test_setattr_type_not_supported():
    def fn():
        MyBox.my_classmethod = 100
        return 1

    with pytest.raises(CompilationError, match="Unsupported field"):
        run_compiled(fn)
