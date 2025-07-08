from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Self, final

from sonolus.backend.ir import IRConst, IRSet
from sonolus.backend.place import BlockPlace
from sonolus.script.array_like import ArrayLike, get_positive_index
from sonolus.script.debug import assert_unreachable
from sonolus.script.internal.context import ctx
from sonolus.script.internal.error import InternalError
from sonolus.script.internal.generic import GenericValue
from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.internal.value import BackingSource, DataValue, Value
from sonolus.script.num import Num


class ArrayMeta(type):
    @meta_fn
    def __pos__[T](cls: type[T]) -> T:
        """Create a zero-initialized array instance."""
        return cls._zero_()


@final
class Array[T, Size](GenericValue, ArrayLike[T], metaclass=ArrayMeta):
    """A fixed size array of values.

    Usage:
        ```python
        array_1 = Array(1, 2, 3)
        array_2 = Array[int, 0]()
        array_3 = +Array[int, 3]  # Create a zero-initialized array
        ```
    """

    _value: list[T] | BlockPlace | BackingSource

    @classmethod
    def element_type(cls) -> type[T] | type[Value]:
        """Return the type of elements in this array type."""
        return cls.type_var_value(T)

    @classmethod
    def size(cls) -> int:
        """Return the size of this array type.

        On instances, use `len(array)` instead.
        """
        return cls.type_var_value(Size)

    def __new__(cls, *args: T) -> Array[T, Any]:
        if cls._type_args_ is None:
            values = [validate_value(arg) for arg in args]
            types = {type(value) for value in values}
            if len(types) == 0:
                raise ValueError(
                    f"{cls.__name__} constructor should be used with at least one value if type is not specified"
                )
            if len(types) > 1:
                raise TypeError(f"{cls.__name__} constructor should be used with values of the same type, got {types}")
            parameterized_cls = cls[types.pop(), len(args)]
        else:
            values = [cls.element_type()._accept_(arg) for arg in args]
            if len(args) != cls.size():
                raise ValueError(f"{cls.__name__} constructor should be used with {cls.size()} values, got {len(args)}")
            parameterized_cls = cls
        if ctx():
            place = ctx().alloc(size=parameterized_cls._size_())
            result: parameterized_cls = parameterized_cls._from_place_(place)
            result._copy_from_(parameterized_cls._with_value(values))
            return result
        else:
            return parameterized_cls._with_value([value._copy_() for value in values])

    def __init__(self, *args: T):
        super().__init__()

    @classmethod
    def _with_value(cls, value) -> Self:
        result = object.__new__(cls)
        result._value = value
        return result

    @classmethod
    def _size_(cls) -> int:
        return cls.size() * cls.element_type()._size_()

    @classmethod
    def _is_value_type_(cls) -> bool:
        return False

    @classmethod
    def _from_backing_source_(cls, source: BackingSource) -> Self:
        return cls._with_value(source)

    @classmethod
    def _from_place_(cls, place: BlockPlace) -> Self:
        return cls._with_value(place)

    @classmethod
    def _accepts_(cls, value: Any) -> bool:
        return isinstance(value, cls)

    @classmethod
    def _accept_(cls, value: Any) -> Self:
        if not cls._accepts_(value):
            raise TypeError(f"Cannot accept value {value} as {cls.__name__}")
        return value

    def _is_py_(self) -> bool:
        return isinstance(self._value, list)

    def _as_py_(self) -> Any:
        if not self._is_py_():
            raise ValueError("Not a python value")
        return self

    @classmethod
    def _from_list_(cls, values: Iterable[DataValue]) -> Self:
        iterator = iter(values)
        return cls(*(cls.element_type()._from_list_(iterator) for _ in range(cls.size())))

    def _to_list_(self, level_refs: dict[Any, str] | None = None) -> list[DataValue | str]:
        match self._value:
            case list():
                return [entry for value in self._value for entry in value._to_list_(level_refs)]
            case BlockPlace():
                return [
                    entry
                    for i in range(self.size())
                    for entry in self.element_type()
                    ._from_place_(self._value.add_offset(i * self.element_type()._size_()))
                    ._to_list_()
                ]
            case backing_source if callable(backing_source):
                return [backing_source(IRConst(i)) for i in range(self.size() * self.element_type()._size_())]
            case _:
                assert_unreachable()

    @classmethod
    def _flat_keys_(cls, prefix: str) -> list[str]:
        return [entry for i in range(cls.size()) for entry in cls.element_type()._flat_keys_(f"{prefix}[{i}]")]

    def _get_(self) -> Self:
        return self

    def _set_(self, value: Self):
        raise TypeError("Array does not support _set_")

    def _copy_from_(self, value: Self):
        if not isinstance(value, type(self)):
            raise TypeError("Cannot copy from different type")
        for i in range(self.size()):
            self[i] = value[i]

    def _copy_(self) -> Self:
        if ctx():
            place = ctx().alloc(size=self._size_())
            result: Self = self._from_place_(place)
            result._copy_from_(self)
            return result
        else:
            assert isinstance(self._value, list)
            return self._with_value([value._copy_() for value in self._value])

    @classmethod
    def _alloc_(cls) -> Self:
        if ctx():
            place = ctx().alloc(size=cls._size_())
            return cls._from_place_(place)
        else:
            return cls._with_value([cls.element_type()._alloc_() for _ in range(cls.size())])

    @classmethod
    def _zero_(cls) -> Self:
        if ctx():
            place = ctx().alloc(size=cls._size_())
            result: Self = cls._from_place_(place)
            ctx().add_statements(*[IRSet(place.add_offset(i), IRConst(0)) for i in range(cls._size_())])
            return result
        else:
            return cls._with_value([cls.element_type()._zero_() for _ in range(cls.size())])

    def __len__(self):
        return self.size()

    @meta_fn
    def __getitem__(self, index: Num) -> T:
        index: Num = Num._accept_(get_positive_index(index, self.size()))
        if index._is_py_() and 0 <= index._as_py_() < self.size():
            const_index = index._as_py_()
            if isinstance(const_index, float) and not const_index.is_integer():
                raise ValueError("Array index must be an integer")
            const_index = int(const_index)
            if isinstance(self._value, list):
                if ctx():
                    return self._value[const_index]._get_()
                else:
                    return self._value[const_index]._get_()._as_py_()
            elif isinstance(self._value, BlockPlace):
                return (
                    self.element_type()
                    ._from_place_(self._value.add_offset(const_index * self.element_type()._size_()))
                    ._get_()
                )
            elif callable(self._value):
                return self.element_type()._from_backing_source_(
                    lambda offset: self._value((Num(offset) + Num(const_index * self.element_type()._size_())).ir())
                )
            else:
                raise InternalError("Unexpected array value")
        else:
            if not ctx():
                raise InternalError("Unexpected non-constant index")
            if isinstance(self._value, list | BlockPlace):
                base = ctx().rom[tuple(self._to_list_())] if isinstance(self._value, list) else self._value
                place = BlockPlace(
                    block=base.block,
                    index=(Num(base.index) + index * self.element_type()._size_()).index(),
                    offset=base.offset,
                )
                return self.element_type()._from_place_(place)._get_()
            elif callable(self._value):
                base_offset = index * Num(self.element_type()._size_())
                return self.element_type()._from_backing_source_(
                    lambda offset: self._value((Num(offset) + base_offset).ir())
                )
            else:
                raise InternalError("Unexpected array value")

    @meta_fn
    def __setitem__(self, index: Num, value: T):
        index: Num = Num._accept_(get_positive_index(index, self.size()))
        value = self.element_type()._accept_(value)
        if ctx():
            if isinstance(self._value, list):
                raise ValueError("Cannot mutate a compile time constant array")
            elif isinstance(self._value, BlockPlace):
                base = self._value
                place = (
                    base.add_offset(int(index._as_py_()) * self.element_type()._size_())
                    if index._is_py_()
                    else BlockPlace(
                        block=base.block,
                        index=(Num(base.index) + index * self.element_type()._size_()).index(),
                        offset=base.offset,
                    )
                )
                dst = self.element_type()._from_place_(place)
            elif callable(self._value):
                base_offset = index * Num(self.element_type()._size_())
                dst = self.element_type()._from_backing_source_(
                    lambda offset: self._value((Num(offset) + base_offset).ir())
                )
            else:
                raise InternalError("Unexpected array value")
            if self.element_type()._is_value_type_():
                dst._set_(value)
            else:
                dst._copy_from_(value)
        else:
            if not isinstance(self._value, list):
                raise InternalError("Unexpected mutation of non compile time constant array")
            const_index = index._as_py_()
            if isinstance(const_index, float) and not const_index.is_integer():
                raise ValueError("Array index must be an integer")
            const_index = int(const_index)
            if not 0 <= const_index < self.size():
                raise IndexError("Array index out of range")
            dst = self._value[const_index]
            if self.element_type()._is_value_type_():
                dst._set_(value)
            else:
                dst._copy_from_(value)

    def __eq__(self, other):
        if not isinstance(other, ArrayLike):
            return False
        if len(self) != len(other):
            return False
        i = 0
        while i < self.size():
            if self[i] != other[i]:
                return False
            i += 1
        return True

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash(tuple(self[i] for i in range(self.size())))

    def __str__(self):
        if isinstance(self._value, BlockPlace) or callable(self._value):
            return f"{type(self).__name__}({self._value}...)"
        else:
            return f"{type(self).__name__}({', '.join(str(self[i]) for i in range(self.size()))})"

    def __repr__(self):
        if isinstance(self._value, BlockPlace) or callable(self._value):
            return f"{type(self).__name__}({self._value}...)"
        else:
            return f"{type(self).__name__}({', '.join(repr(self[i]) for i in range(self.size()))})"

    @meta_fn
    def __pos__(self) -> Self:
        """Return a copy of the array."""
        return self._copy_()
