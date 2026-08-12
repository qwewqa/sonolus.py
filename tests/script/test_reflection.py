import pytest

from sonolus.script.array import Array
from sonolus.script.array_like import ArrayLike
from sonolus.script.debug import debug_log
from sonolus.script.internal.descriptor import SonolusDescriptor
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.value import BackingValue
from sonolus.script.num import Num
from sonolus.script.record import Record
from sonolus.script.vec import Vec2
from tests.script.conftest import run_and_validate, run_compiled


class UnsupportedDescriptor:
    def __get__(self, instance, owner):
        return -1

    def __set__(self, instance, value):
        pass


class MissingDescriptor(SonolusDescriptor):
    def __get__(self, instance, owner):
        raise AttributeError

    def __set__(self, instance, value):
        pass


def missing_property(self):
    return self.does_not_exist


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

    @property
    def logging_property(self):
        debug_log(123)
        return 789


MyBox.unsupported_descriptor = UnsupportedDescriptor()
MyBox.missing_descriptor = MissingDescriptor()
MyBox.missing_property = property(missing_property)


class GetterlessPropertyBox(Record):
    missing = property()


class PropertyFallbackBox(Record):
    @property
    def fallback(self):
        return self.does_not_exist

    def __getattr__(self, name):
        if name == "fallback":
            return 321
        raise AttributeError(name)


@meta_fn
def missing_attribute(_self):
    raise AttributeError("missing")


def property_with_transient_closure_then_attribute_error(self):
    z = 1
    get_z = lambda: z  # noqa: E731
    get_z()
    z = 2
    return missing_attribute(self)


class ClassMethodPropertyFallbackBox(Record):
    fallback = property(missing_attribute)

    @classmethod
    def __getattr__(cls, name):
        return 432


class StaticMethodPropertyFallbackBox(Record):
    fallback = property(missing_attribute)

    @staticmethod
    def __getattr__(name):
        return 543


class TransientClosurePropertyFallbackBox(Record):
    fallback = property(property_with_transient_closure_then_attribute_error)

    def __getattr__(self, name):
        return 2


class PropertyDefaultBox(Record):
    @property
    def missing(self):
        return self.does_not_exist


class TracedGetattrBox(Record):
    value: Num

    def __getattr__(self, name):
        return 10 if self.value else 20


class ClassMethodGetattrBox(Record):
    @classmethod
    def __getattr__(cls, name):
        return 31


class StaticMethodGetattrBox(Record):
    @staticmethod
    def __getattr__(name):
        return 32


class ConditionalAttributeErrorPropertyBox(Record):
    flag: Num

    @property
    def property(self):
        if self.flag:
            return self.missing
        return 5


@meta_fn
def outer_exception_from_attribute_error(_self):
    raise ValueError("outer") from AttributeError("inner")


class OuterExceptionFromAttributeErrorBox(Record):
    failing = property(outer_exception_from_attribute_error)


Record.masked_for_test = property(lambda self: 123)
MyBox.masked_for_test = None


def _direct_meta_function():
    return 1


def test_unimplemented_sonolus_descriptor_methods_raise():
    descriptor = SonolusDescriptor()
    with pytest.raises(NotImplementedError):
        descriptor.__get__(None, object)
    with pytest.raises(NotImplementedError):
        descriptor.__set__(None, 0)


def test_unimplemented_backing_value_methods_raise():
    backing = BackingValue()
    with pytest.raises(NotImplementedError):
        backing.read()
    with pytest.raises(NotImplementedError):
        backing.write(0)


def test_meta_fn_accepts_direct_function_configuration():
    assert meta_fn(_direct_meta_function, show_in_stack=False)() == 1


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


def test_hasattr_invokes_property_getter():
    def fn():
        return hasattr(MyBox(1), "logging_property")

    assert run_and_validate(fn) == 1


def test_hasattr_suppresses_attribute_error_from_descriptor():
    def fn():
        return hasattr(MyBox(1), "missing_descriptor")

    assert run_and_validate(fn) == 0


def test_hasattr_suppresses_attribute_error_from_property():
    def fn():
        return hasattr(MyBox(1), "missing_property")

    with pytest.raises(CompilationError, match="AttributeError propagation from a traced property getter"):
        run_compiled(fn)


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


def test_direct_missing_attribute_traces_getattr():
    def fn():
        return TracedGetattrBox(1).missing

    assert run_and_validate(fn) == 10


def test_builtin_getattr_of_missing_attribute_traces_getattr():
    def fn():
        return getattr(TracedGetattrBox(0), "missing")  # noqa: B009

    assert run_and_validate(fn) == 20


def test_hasattr_of_missing_attribute_traces_getattr():
    def fn():
        return hasattr(TracedGetattrBox(1), "missing")

    assert run_and_validate(fn)


def test_ordinary_missing_attribute_binds_classmethod_getattr():
    def fn():
        box = ClassMethodGetattrBox()
        return box.missing + getattr(box, "other") + hasattr(box, "third")  # noqa: B009

    assert run_and_validate(fn) == 63


def test_ordinary_missing_attribute_binds_staticmethod_getattr():
    def fn():
        box = StaticMethodGetattrBox()
        return box.missing + getattr(box, "other") + hasattr(box, "third")  # noqa: B009

    assert run_and_validate(fn) == 65


@pytest.mark.parametrize("kind", ["direct", "getattr", "hasattr"])
def test_conditional_property_attribute_error_is_rejected(kind):
    def fn():
        box = ConditionalAttributeErrorPropertyBox(Array(0)[0])
        if kind == "direct":
            return box.property
        if kind == "getattr":
            return getattr(box, "property")  # noqa: B009
        return hasattr(box, "property")

    with pytest.raises(CompilationError, match="AttributeError propagation from a traced property getter"):
        run_compiled(fn)


def test_direct_attribute_uses_getattr_after_property_attribute_error():
    def fn():
        x = 5
        y = PropertyFallbackBox().fallback
        return x + y

    with pytest.raises(CompilationError, match="Raise statements are not supported"):
        run_compiled(fn)


def test_builtin_getattr_uses_getattr_after_property_attribute_error():
    def fn():
        x = 5
        y = getattr(PropertyFallbackBox(), "fallback")  # noqa: B009
        return x + y

    with pytest.raises(CompilationError, match="Raise statements are not supported"):
        run_compiled(fn)


def test_direct_attribute_binds_classmethod_getattr():
    def fn():
        return ClassMethodPropertyFallbackBox().fallback

    with pytest.raises(CompilationError, match="AttributeError propagation from a traced property getter"):
        run_compiled(fn)


def test_builtin_getattr_binds_classmethod_getattr():
    def fn():
        return getattr(ClassMethodPropertyFallbackBox(), "fallback")  # noqa: B009

    with pytest.raises(CompilationError, match="AttributeError propagation from a traced property getter"):
        run_compiled(fn)


def test_direct_attribute_binds_staticmethod_getattr():
    def fn():
        return StaticMethodPropertyFallbackBox().fallback

    with pytest.raises(CompilationError, match="AttributeError propagation from a traced property getter"):
        run_compiled(fn)


def test_builtin_getattr_binds_staticmethod_getattr():
    def fn():
        return getattr(StaticMethodPropertyFallbackBox(), "fallback")  # noqa: B009

    with pytest.raises(CompilationError, match="AttributeError propagation from a traced property getter"):
        run_compiled(fn)


def test_generator_property_fallback_does_not_capture_transient_getter_local():
    def fn():
        def gen():
            yield TransientClosurePropertyFallbackBox().fallback
            yield TransientClosurePropertyFallbackBox().fallback

        iterator = gen()
        return next(iterator) + next(iterator)

    with pytest.raises(CompilationError, match="AttributeError propagation from a traced property getter"):
        run_compiled(fn)


def test_builtin_getattr_default_handles_property_attribute_error():
    def fn():
        x = 5
        y = getattr(PropertyDefaultBox(), "missing", 654)
        return x + y

    with pytest.raises(CompilationError, match="AttributeError propagation from a traced property getter"):
        run_compiled(fn)


def test_hasattr_continues_with_caller_scope_after_property_attribute_error():
    def fn():
        x = 5
        found = hasattr(PropertyDefaultBox(), "missing")
        return x + found

    with pytest.raises(CompilationError, match="AttributeError propagation from a traced property getter"):
        run_compiled(fn)


def test_getterless_property_is_an_unreadable_attribute():
    def fn():
        return GetterlessPropertyBox().missing

    with pytest.raises(AttributeError, match=r"property 'missing'.*has no getter"):
        run_and_validate(fn)


def test_hasattr_propagates_outer_exception_caused_by_attribute_error():
    def fn():
        return hasattr(OuterExceptionFromAttributeErrorBox(), "failing")

    with pytest.raises(ValueError, match="outer"):
        run_and_validate(fn)


def test_attribute_none_masks_inherited_property():
    def fn():
        return MyBox(1).masked_for_test is None

    assert run_and_validate(fn)


def test_builtin_getattr_none_masks_inherited_property():
    def fn():
        return getattr(MyBox(1), "masked_for_test") is None  # noqa: B009

    assert run_and_validate(fn)


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


def test_issubclass_record_subclass():
    def fn():
        return issubclass(MyBox, Record)

    assert run_and_validate(fn)


def test_issubclass_not_subclass():
    def fn():
        return issubclass(Num, Record)

    assert not run_and_validate(fn)


def test_issubclass_generic_specialization():
    def fn():
        return issubclass(MyBox[Num], MyBox)

    assert run_and_validate(fn)


def test_issubclass_array_like():
    def fn():
        return issubclass(Array[Num, 2], ArrayLike)

    assert run_and_validate(fn)


def test_issubclass_of_type_result():
    def fn():
        return issubclass(type(MyBox(1)), Record)

    assert run_and_validate(fn)


@pytest.mark.parametrize("builtin", [int, float, bool, set, dict, type])
def test_type_of_builtin_alias_is_type(builtin):
    def fn():
        return type(builtin) == type  # noqa: E721

    assert run_and_validate(fn)


def test_issubclass_dict():
    def fn():
        return issubclass(dict, dict)

    assert run_and_validate(fn)


def test_issubclass_dict_not_set():
    def fn():
        return issubclass(dict, set)

    assert not run_and_validate(fn)


# dict and set are represented internally by Record subclasses. The builtin aliases must not expose that, since
# plain Python answers False for every one of these.


@pytest.mark.parametrize("builtin", [set, dict, tuple])
def test_builtin_alias_is_not_a_record_subclass(builtin):
    def fn():
        return issubclass(builtin, Record)

    assert not run_and_validate(fn)


@pytest.mark.parametrize("value", [{1, 2}, {1: 2}, (1, 2)])
def test_builtin_alias_value_is_not_a_record_instance(value):
    def fn():
        return isinstance(value, Record)

    assert not run_and_validate(fn)


def test_issubclass_int_not_supported():
    def fn():
        return issubclass(Num, int)

    with pytest.raises(CompilationError, match="use Num instead"):
        run_compiled(fn)


def test_issubclass_instance_not_supported():
    def fn():
        return issubclass(MyBox(1), Record)

    with pytest.raises(CompilationError, match="arg 1 must be a class"):
        run_compiled(fn)


def test_isinstance_frozenset_not_supported():
    # frozenset values are represented as ordinary sets, so there is no frozenset type to check against.
    s = {1, 2}

    def fn():
        return isinstance(s, frozenset)

    with pytest.raises(CompilationError, match="against frozenset is not supported"):
        run_compiled(fn)


def test_issubclass_frozenset_not_supported():
    # A directional check between set and frozenset can't match Python's answer, so it is rejected
    # rather than answered incorrectly.
    def fn():
        return issubclass(frozenset, set)

    with pytest.raises(CompilationError, match="against frozenset is not supported"):
        run_compiled(fn)


def test_issubclass_unhashable_arg_reports_cleanly():
    # Set and dict values are unhashable, so the type aliases must be compared by identity rather than
    # by set membership, which would surface an internal error instead of this one.
    s = {1, 2}

    def fn():
        return issubclass(s, set)

    with pytest.raises(CompilationError, match="arg 1 must be a class"):
        run_compiled(fn)


def test_isinstance_unhashable_type_arg_reports_cleanly():
    s = {1, 2}

    def fn():
        return isinstance(MyBox(1), s)

    with pytest.raises(CompilationError, match="Unsupported type"):
        run_compiled(fn)


def test_issubclass_runtime_class_not_supported():
    def fn():
        x = 0
        for i in range(3):
            x += i
        return issubclass(x, Record)

    with pytest.raises(CompilationError, match="arg 1 must be a class known at compile time"):
        run_compiled(fn)


def test_isinstance_runtime_classinfo_not_supported():
    # Mirrors the issubclass guard below: both fold at compile time, so both reject a runtime classinfo the
    # same way rather than one of them leaking an internal error.
    def fn():
        x = 0
        for i in range(3):
            x += i
        return isinstance(MyBox(1), x)

    with pytest.raises(CompilationError, match="arg 2 must be a class known at compile time"):
        run_compiled(fn)


def test_issubclass_runtime_classinfo_not_supported():
    def fn():
        x = 0
        for i in range(3):
            x += i
        return issubclass(MyBox, x)

    with pytest.raises(CompilationError, match="arg 2 must be a class known at compile time"):
        run_compiled(fn)


# classinfo may be a tuple of types, as in Python: the check is true if any member matches. The tuple is a
# compile-time construct, so the whole thing still folds to a compile-time boolean.


def test_isinstance_tuple_matches_first_member():
    def fn():
        return isinstance(Vec2(1, 2), (Vec2, Num))

    assert run_and_validate(fn)


def test_isinstance_tuple_matches_second_member():
    def fn():
        return isinstance(Vec2(1, 2), (Num, Vec2))

    assert run_and_validate(fn)


def test_isinstance_tuple_matches_no_member():
    def fn():
        return isinstance(Vec2(1, 2), (Num, MyBox))

    assert not run_and_validate(fn)


def test_isinstance_single_element_tuple():
    def fn():
        return isinstance(Vec2(1, 2), (Vec2,))

    assert run_and_validate(fn)


def test_isinstance_empty_tuple():
    def fn():
        return isinstance(Vec2(1, 2), ())

    assert not run_and_validate(fn)


def test_isinstance_nested_tuple():
    def fn():
        return isinstance(Vec2(1, 2), (Num, (MyBox, Vec2)))

    assert run_and_validate(fn)


def test_isinstance_nested_tuple_matches_no_member():
    def fn():
        return isinstance(Vec2(1, 2), (Num, (MyBox, ArrayLike)))

    assert not run_and_validate(fn)


def test_isinstance_tuple_array_like_member():
    # ArrayLike opts in via _allow_instance_check_ rather than being a Value subclass, so the tuple path has to
    # accept it too.
    def fn():
        return isinstance(Array(1, 2), (Num, ArrayLike))

    assert run_and_validate(fn)


def test_isinstance_tuple_dict_alias():
    def fn():
        d = {1: 2}
        return isinstance(d, (dict, Num))

    assert run_and_validate(fn)


def test_isinstance_tuple_set_alias():
    def fn():
        s = {1, 2}
        return isinstance(s, (set, Num))

    assert run_and_validate(fn)


def test_isinstance_tuple_tuple_alias():
    def fn():
        t = (1, 2)
        return isinstance(t, (tuple, Num))

    assert run_and_validate(fn)


def test_isinstance_tuple_short_circuits_before_unsupported_member():
    def fn():
        return isinstance(Vec2(1, 2), (Vec2, int))

    assert run_and_validate(fn)


def test_isinstance_tuple_float_member_not_supported():
    def fn():
        return isinstance(Vec2(1, 2), (MyBox, float))

    with pytest.raises(CompilationError, match="use Num instead"):
        run_compiled(fn)


def test_isinstance_tuple_bool_member_not_supported():
    def fn():
        return isinstance(Vec2(1, 2), (MyBox, bool))

    with pytest.raises(CompilationError, match="use Num instead"):
        run_compiled(fn)


def test_isinstance_tuple_frozenset_member_not_supported():
    def fn():
        return isinstance(Vec2(1, 2), (MyBox, frozenset))

    with pytest.raises(CompilationError, match="against frozenset is not supported"):
        run_compiled(fn)


def test_isinstance_tuple_short_circuits_before_non_type_member():
    def fn():
        return isinstance(Vec2(1, 2), (Vec2, None))

    assert run_and_validate(fn)


def test_isinstance_tuple_short_circuits_before_invalid_nested_tuple():
    def fn():
        return isinstance(Vec2(1, 2), (Vec2, (Num, None)))

    assert run_and_validate(fn)


def test_issubclass_tuple_matches_first_member():
    def fn():
        return issubclass(MyBox, (MyBox, Record))

    assert run_and_validate(fn)


def test_issubclass_tuple_matches_second_member():
    def fn():
        return issubclass(MyBox, (Num, Record))

    assert run_and_validate(fn)


def test_issubclass_tuple_matches_no_member():
    def fn():
        return issubclass(Num, (Record, ArrayLike))

    assert not run_and_validate(fn)


def test_issubclass_single_element_tuple():
    def fn():
        return issubclass(MyBox, (Record,))

    assert run_and_validate(fn)


def test_issubclass_empty_tuple():
    def fn():
        return issubclass(MyBox, ())

    assert not run_and_validate(fn)


def test_issubclass_nested_tuple():
    def fn():
        return issubclass(MyBox, (Num, (ArrayLike, Record)))

    assert run_and_validate(fn)


def test_issubclass_nested_tuple_matches_no_member():
    def fn():
        return issubclass(Num, (Record, (ArrayLike, MyBox)))

    assert not run_and_validate(fn)


def test_issubclass_tuple_array_like_member():
    def fn():
        return issubclass(Array[Num, 2], (Record, ArrayLike))

    assert run_and_validate(fn)


def test_issubclass_tuple_dict_alias():
    def fn():
        return issubclass(dict, (dict, Num))

    assert run_and_validate(fn)


def test_issubclass_tuple_set_alias():
    def fn():
        return issubclass(set, (Num, set))

    assert run_and_validate(fn)


def test_issubclass_tuple_tuple_alias():
    def fn():
        return issubclass(tuple, (Num, tuple))

    assert run_and_validate(fn)


def test_issubclass_tuple_short_circuits_before_unsupported_member():
    def fn():
        return issubclass(MyBox, (Record, int))

    assert run_and_validate(fn)


def test_issubclass_tuple_short_circuits_before_frozenset_member():
    def fn():
        return issubclass(MyBox, (Record, frozenset))

    assert run_and_validate(fn)


def test_issubclass_tuple_short_circuits_before_non_type_member():
    def fn():
        return issubclass(MyBox, (Record, None))

    assert run_and_validate(fn)


def test_issubclass_tuple_as_arg_1_not_supported():
    # A tuple is only accepted for classinfo, matching Python, which rejects it as arg 1.
    def fn():
        return issubclass((MyBox, Record), Record)

    with pytest.raises(CompilationError, match="arg 1 must be a class"):
        run_compiled(fn)


def test_issubclass_runtime_value_in_classinfo_tuple_not_supported():
    def fn():
        x = 0
        for i in range(3):
            x += i
        return issubclass(MyBox, (Record, x))

    with pytest.raises(CompilationError, match="arg 2 must be a class known at compile time"):
        run_compiled(fn)
