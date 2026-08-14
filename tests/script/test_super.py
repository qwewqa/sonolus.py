import pytest

from sonolus.script.array import Array
from sonolus.script.debug import debug_log
from sonolus.script.internal.error import CompilationError
from sonolus.script.record import Record
from tests.script.conftest import run_and_validate, run_compiled


class A:
    def m(self):
        debug_log(1)

    @classmethod
    def m_cls(cls):
        debug_log(1)


class B(A):
    def m(self):
        debug_log(2)
        super().m()
        debug_log(3)

    @classmethod
    def m_cls(cls):
        debug_log(2)
        super().m_cls()
        debug_log(3)


class C(A):
    def m(self):
        debug_log(4)
        super().m()
        debug_log(5)

    @classmethod
    def m_cls(cls):
        debug_log(4)
        super().m_cls()
        debug_log(5)


class D(B, C):
    def m(self):
        debug_log(6)
        super().m()
        debug_log(7)

    @classmethod
    def m_cls(cls):
        debug_log(6)
        super().m_cls()
        debug_log(7)


class PropertyMixin:
    @property
    def choice(self):
        return 10 if self.value else 20


class PropertyRecord(PropertyMixin, Record):
    value: float

    @property
    def direct_choice(self):
        return self.choice

    @property
    def super_choice(self):
        return super().choice

    @property
    def super_choice_with_getattr(self):
        return getattr(super(), "choice")  # ruff: ignore[get-attr-with-constant]


class ShadowingBase:
    __self__ = None
    __self_class__ = None
    __thisclass__ = None

    def value(self):
        return 7


class ShadowingChild(ShadowingBase):
    def value(self):
        return super().value() + 1

    def value_with_getattr(self):
        return getattr(super(), "value")() + 1  # ruff: ignore[get-attr-with-constant]

    def proxy_class(self):
        return super().__class__ == super  # ruff: ignore[type-comparison]

    def proxy_class_with_getattr(self):
        return getattr(super(), "__class__") == super  # ruff: ignore[get-attr-with-constant, type-comparison]


class LexicalSuperBase:
    def value(self):
        return 7


class LexicalSuperChild(LexicalSuperBase):
    def nested_value(self):
        def read(value):
            return super().value()

        return read(self) + 1


class ShadowedNestedClassCellChild(LexicalSuperBase):
    def nested_value(self):
        super().value()

        def read(value):
            __class__ = LexicalSuperChild  # ruff: ignore[unused-variable]
            return super().value()

        return read(self)


def module_global_super_attribute(value):
    return super().value()


def module_global_super_getattr(value):
    return getattr(super(), "value")()  # ruff: ignore[get-attr-with-constant]


def make_lexical_super_reader():
    __class__ = LexicalSuperChild  # ruff: ignore[unused-variable]

    def read(value):
        return super().value()

    return read


class ClassBoundPropertyBase:
    @property
    def property(self):
        debug_log(9)
        return 7


class ClassBoundPropertyChild(ClassBoundPropertyBase):
    @classmethod
    def read_property(cls):
        _ = super().property
        return 1

    @classmethod
    def read_property_with_getattr(cls):
        _ = getattr(super(), "property")  # ruff: ignore[get-attr-with-constant]
        return 1


def read_class_bound_property():
    return ClassBoundPropertyChild.read_property()


def read_class_bound_property_with_getattr():
    return ClassBoundPropertyChild.read_property_with_getattr()


def test_super_simple():
    b = B()
    b._is_comptime_value_ = True  # type: ignore

    def fn():
        b.m()

    run_and_validate(fn)


def test_super_diamond():
    d = D()
    d._is_comptime_value_ = True  # type: ignore

    def fn():
        d.m()

    run_and_validate(fn)


def test_super_classmethod():
    b = B()
    b._is_comptime_value_ = True  # type: ignore

    def fn():
        b.m_cls()

    run_and_validate(fn)


def test_super_classmethod_diamond():
    d = D()
    d._is_comptime_value_ = True  # type: ignore

    def fn():
        d.m_cls()

    run_and_validate(fn)


def test_super_property():
    def fn():
        value = Array(0)[0]
        record = PropertyRecord(value)
        return record.direct_choice * 100 + record.super_choice

    run_and_validate(fn)


def test_super_property_with_getattr():
    def fn():
        value = Array(0)[0]
        return PropertyRecord(value).super_choice_with_getattr

    run_and_validate(fn)


def test_super_proxy_metadata_is_not_shadowed():
    child = ShadowingChild()
    child._is_comptime_value_ = True  # type: ignore

    def fn():
        return (
            child.value() * 1000
            + child.value_with_getattr() * 100
            + child.proxy_class() * 10
            + child.proxy_class_with_getattr()
        )

    run_and_validate(fn)


def test_zero_argument_super_in_nested_method_uses_lexical_class_cell():
    child = LexicalSuperChild()
    child._is_comptime_value_ = True  # type: ignore

    def fn():
        return child.nested_value()

    assert run_and_validate(fn) == 8


def test_zero_argument_super_accepts_an_enclosing_function_class_cell():
    child = LexicalSuperChild()
    child._is_comptime_value_ = True  # type: ignore

    assert run_and_validate(make_lexical_super_reader(), child) == 7


def test_nested_local_class_does_not_inherit_the_method_class_cell():
    child = ShadowedNestedClassCellChild()
    child._is_comptime_value_ = True  # type: ignore

    def fn():
        return child.nested_value()

    with pytest.raises(RuntimeError, match=r"super\(\): __class__ cell not found"):
        run_and_validate(fn)


@pytest.mark.parametrize("fn", [module_global_super_attribute, module_global_super_getattr])
def test_module_global_class_does_not_enable_zero_argument_super(fn, monkeypatch):
    child = LexicalSuperChild()
    child._is_comptime_value_ = True  # type: ignore
    monkeypatch.setitem(fn.__globals__, "__class__", LexicalSuperChild)

    with pytest.raises(RuntimeError, match=r"super\(\): __class__ cell not found"):
        run_and_validate(fn, child)


@pytest.mark.parametrize(
    "fn",
    [read_class_bound_property, read_class_bound_property_with_getattr],
)
def test_class_bound_super_does_not_invoke_property_getter(fn):
    # run_compiled pins the accepted class-property rejection; the getter must not execute during compilation.
    with pytest.raises(CompilationError, match="Unsupported value: property object"):
        run_compiled(fn)


def test_unbound_super_getattr_default():
    def fn():
        return getattr(super(A), "missing", 7)

    run_and_validate(fn)


def test_unbound_super_missing_attribute():
    def fn():
        return super(A).missing

    with pytest.raises(AttributeError, match="'super' object has no attribute 'missing'"):
        run_and_validate(fn)
