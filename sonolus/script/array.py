# ruff: noqa: A005
from __future__ import annotations

from typing import final, Self, Iterable, Any

from sonolus.backend.place import BlockPlace
from sonolus.script.internal.context import ctx
from sonolus.script.internal.error import InternalError
from sonolus.script.internal.generic import GenericValue
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.value import Value
from sonolus.script.num import Num


@final
class Array[T, Size](GenericValue):
    _value: list[T] | BlockPlace

    @classmethod
    def element_type(cls) -> type[T] | type[Value]:
        return cls.get_type_arg_(T)

    @classmethod
    def size(cls) -> int:
        return cls.get_type_arg_(Size)

    def __init__(self, *args, **kwargs):
        # We could use __new__ instead of Array.of, but making it a classmethod plays nicer with typing
        raise TypeError("Array cannot be directly instantiated, use Array.of or alloc instead")

    @classmethod
    def of[R](cls, *args: R) -> Array[R, len(values)]:
        if cls.type_args_ is None:
            values = [validate_value(arg) for arg in args]
            types = {type(value) for value in values}
            if len(types) == 0:
                raise ValueError(f"{cls.__name__}.of() should be used with at least one value if type is not specified")
            if len(types) > 1:
                raise TypeError(f"{cls.__name__}.of() should be used with values of the same type, got {types}")
            parameterized_cls = cls[types.pop(), len(args)]
        else:
            values = [cls.element_type().accept_(arg) for arg in args]
            if len(args) != cls.size():
                raise ValueError(f"{cls.__name__}.of() should be used with {cls.size()} values, got {len(args)}")
            parameterized_cls = cls
        if ctx():
            place = ctx().alloc(size=parameterized_cls.size())
            result: parameterized_cls = parameterized_cls.from_place_(place)
            result.copy_from_(parameterized_cls._with_value(values))
            return result
        else:
            return cls._with_value([value.copy_() for value in values])

    @classmethod
    def _with_value(cls, value) -> Self:
        result = object.__new__(cls)
        result._value = value
        return result

    @classmethod
    def size_(cls) -> int:
        return cls.size() * cls.element_type().size_()

    @classmethod
    def is_value_type_(cls) -> bool:
        return False

    @classmethod
    def from_place_(cls, place: BlockPlace) -> Self:
        return cls._with_value(place)

    @classmethod
    def accepts_(cls, value: Any) -> bool:
        return isinstance(value, cls)

    @classmethod
    def accept_(cls, value: Any) -> Self:
        if not cls.accepts_(value):
            raise TypeError(f"Cannot accept value {value} as {cls.__name__}")
        return value

    def is_py_(self) -> bool:
        return isinstance(self._value, list)

    def as_py_(self) -> Any:
        if not self.is_py_():
            raise ValueError("Not a python value")
        return self

    @classmethod
    def from_list_(cls, values: Iterable[float]) -> Self:
        iterator = iter(values)
        return cls.of(*(cls.element_type().from_list_(iterator) for _ in range(cls.size())))

    def to_list_(self) -> list[float]:
        return [entry for value in self._value for entry in value.to_list_()]

    def get_(self) -> Self:
        return self

    def set_(self, value: Self):
        raise TypeError("Array does not support set_")

    def copy_from_(self, value: Self):
        if not isinstance(value, type(self)):
            raise TypeError("Cannot copy from different type")
        for i in range(self.size()):
            self[i] = value[i]

    def copy_(self) -> Self:
        if ctx():
            place = ctx().alloc(size=self.size())
            result: Self = self.from_place_(place)
            result.copy_from_(self)
            return result
        else:
            return self._with_value([value.copy_() for value in self._value])

    def __getitem__(self, index: Num) -> T:
        index: Num = Num.accept_(index)
        if index.is_py_():
            const_index = index.as_py_()
            if isinstance(const_index, float) and not const_index.is_integer():
                raise ValueError("Array index must be an integer")
            const_index = int(const_index)
            if not 0 <= const_index < self.size():
                raise IndexError("Array index out of range")
            if isinstance(self._value, list):
                return self._value[const_index]
            else:
                return self.element_type().from_place_(self._value.add_offset(const_index * self.element_type().size_()))
        else:
            if not ctx():
                raise InternalError("Unexpected non-constant index")
            # TODO