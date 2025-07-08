from __future__ import annotations

import inspect
from collections.abc import Iterable
from inspect import getmro
from typing import Any, ClassVar, Self, TypeVar, dataclass_transform, get_origin

from sonolus.backend.place import BlockPlace
from sonolus.script.internal.context import ctx
from sonolus.script.internal.descriptor import SonolusDescriptor
from sonolus.script.internal.generic import (
    GenericValue,
    accept_and_infer_types,
    validate_and_resolve_type,
    validate_concrete_type,
    validate_type_spec,
)
from sonolus.script.internal.impl import meta_fn
from sonolus.script.internal.value import BackingSource, DataValue, Value
from sonolus.script.num import Num


class RecordMeta(type):
    @meta_fn
    def __pos__[T](cls: type[T]) -> T:
        """Create a zero-initialized record instance."""
        return cls._zero_()


@dataclass_transform(eq_default=True)
class Record(GenericValue, metaclass=RecordMeta):
    """Base class for user-defined data structures.

    Usage:
        A regular record:
        ```python
        class MyRecord(Record):
            field1: int
            field2: bool
        ```

        A generic record:
        ```python
        class MyGenericRecord[T, U](Record):
            field1: T
            field2: U
        ```

        Creating an instance:
        ```python
        record = MyRecord(field1=42, field2=True)
        record_2 = MyGenericRecord[int, int](field1=42, field2=100)
        record_3 = MyGenericRecord(field1=42, field2=100)  # Type arguments can be inferred
        record_4 = +MyRecord  # Create a zero-initialized record
        record_5 = +MyGenericRecord[int, int]
        ```
    """

    _value: dict[str, Value]
    _fields: ClassVar[list[_RecordField] | None] = None
    _constructor_signature: ClassVar[inspect.Signature]

    @classmethod
    def _validate_type_args_(cls, args: tuple[Any, ...]) -> tuple[Any, ...]:
        if cls._fields is None:
            raise TypeError("Base Record class cannot have type arguments")
        return super()._validate_type_args_(args)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()
        is_parameterizing = cls._type_args_ is not None and all(
            getattr(parent, "_type_args_", None) is None for parent in getmro(cls)[1:]
        )
        if is_parameterizing:
            fields = []
            offset = 0
            for generic_field in cls._fields:
                resolved_type = validate_and_resolve_type(generic_field.type, cls._type_vars_to_args_)
                resolved_type = validate_concrete_type(resolved_type)
                field = _RecordField(generic_field.name, resolved_type, generic_field.index, offset)
                fields.append(field)
                setattr(cls, field.name, field)
                offset += resolved_type._size_()
            cls._fields = fields
            return
        is_inheriting_from_existing_record_class = cls._fields is not None
        if is_inheriting_from_existing_record_class and not is_parameterizing:
            # The main reason this is disallowed is that subclasses wouldn't be substitutable for their parent classes
            # Assignment of a subclass instance to a variable of the parent class would either be disallowed or would
            # require object slicing. Either way, it could lead to confusion.
            # Dealing with generic supertypes is also tricky, so it isn't really worth the effort to support this.
            raise TypeError("Subclassing of a Record is not supported")

        hints = inspect.get_annotations(cls, eval_str=True)
        fields = []
        params = []
        index = 0
        offset = 0
        for name, hint in hints.items():
            if name not in cls.__annotations__:
                continue
            if hint is ClassVar or get_origin(hint) is ClassVar:
                continue
            if hasattr(cls, name):
                raise TypeError("Default values are not supported for Record fields")
            type_ = validate_type_spec(hint)
            fields.append(_RecordField(name, type_, index, offset))
            if isinstance(type_, type) and issubclass(type_, Value) and type_._is_concrete_():
                offset += type_._size_()
            setattr(cls, name, fields[-1])
            index += 1
            params.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=type_,
                )
            )

        cls._parameterized_ = {}
        cls._fields = fields
        cls._constructor_signature = inspect.Signature(params)

        _add_inplace_ops(cls)

        cls.__match_args__ = tuple(field.name for field in fields)

        if len(getattr(cls, "__type_params__", ())) == 0:
            # Make the class behave as the parameterized version
            cls._type_args_ = ()
            cls._type_vars_to_args_ = {}
            cls._parameterized_[()] = cls

    def __new__(cls, *args, **kwargs):
        # We override __new__ to allow changing to the parameterized version
        if cls._constructor_signature is None:
            raise TypeError(f"Cannot instantiate {cls.__name__}")
        bound = cls._constructor_signature.bind(*args, **kwargs)
        bound.apply_defaults()
        values = {}
        type_vars = {}
        for field in cls._fields:
            value = bound.arguments[field.name]
            value = accept_and_infer_types(field.type, value, type_vars)
            values[field.name] = value._get_()
        for type_param in cls.__type_params__:
            if type_param not in type_vars:
                raise TypeError(f"Type parameter {type_param} cannot be inferred and must be provided explicitly")
        type_args = tuple(type_vars[type_param] for type_param in cls.__type_params__)
        if cls._type_args_ is not None:
            parameterized = cls
        else:
            parameterized = cls[type_args]
        result: cls = object.__new__(parameterized)  # type: ignore
        result._value = values
        return result

    def __init__(self, *args, **kwargs):
        # Initialization is done in __new__ and other methods
        pass

    @classmethod
    def _raw(cls, **kwargs) -> Self:
        result = object.__new__(cls)
        result._value = kwargs
        return result

    @classmethod
    def _size_(cls) -> int:
        return sum(field.type._size_() for field in cls._fields)

    @classmethod
    def _is_value_type_(cls) -> bool:
        return False

    @classmethod
    def _from_backing_source_(cls, source: BackingSource) -> Self:
        result = object.__new__(cls)
        result._value = {
            field.name: field.type._from_backing_source_(
                lambda offset, field_offset=field.offset: source((Num(offset) + Num(field_offset)).ir())
            )
            for field in cls._fields
        }
        return result

    @classmethod
    def _from_place_(cls, place: BlockPlace) -> Self:
        result = object.__new__(cls)
        result._value = {field.name: field.type._from_place_(place.add_offset(field.offset)) for field in cls._fields}
        return result

    @classmethod
    def _accepts_(cls, value: Any) -> bool:
        return issubclass(type(value), cls)

    @classmethod
    def _accept_(cls, value: Any) -> Self:
        if not cls._accepts_(value):
            raise TypeError(f"Cannot accept value {value} as {cls.__name__}")
        return value

    def _is_py_(self) -> bool:
        return all(value._is_py_() for value in self._value.values())

    def _as_py_(self) -> Self:
        if not self._is_py_():
            raise ValueError("Not a python value")
        return self

    @classmethod
    def _from_list_(cls, values: Iterable[DataValue]) -> Self:
        iterator = iter(values)
        return cls(**{field.name: field.type._from_list_(iterator) for field in cls._fields})

    def _to_list_(self, level_refs: dict[Any, str] | None = None) -> list[DataValue | str]:
        result = []
        for field in self._fields:
            result.extend(self._value[field.name]._to_list_(level_refs))
        return result

    @classmethod
    def _flat_keys_(cls, prefix: str) -> list[str]:
        result = []
        for field in cls._fields:
            result.extend(field.type._flat_keys_(f"{prefix}.{field.name}"))
        return result

    def _get_(self) -> Self:
        return self

    def _set_(self, value: Self):
        raise TypeError("Record does not support _set_")

    def _copy_from_(self, value: Self):
        value = self._accept_(value)
        for field in self._fields:
            field.__set__(self, field.__get__(value))

    def _copy_(self) -> Self:
        return type(self)._raw(**{field.name: self._value[field.name]._copy_() for field in self._fields})

    @classmethod
    def _alloc_(cls) -> Self:
        # Compared to using the constructor, this avoids unnecessary _get_ calls
        result = object.__new__(cls)
        result._value = {field.name: field.type._alloc_() for field in cls._fields}
        return result

    @classmethod
    def _zero_(cls) -> Self:
        result = object.__new__(cls)
        result._value = {field.name: field.type._zero_() for field in cls._fields}
        return result

    def __str__(self):
        return (
            f"{self.__class__.__name__}({', '.join(f'{field.name}={field.__get__(self)}' for field in self._fields)})"
        )

    def __repr__(self):
        return (
            f"{self.__class__.__name__}({', '.join(f'{field.name}={field.__get__(self)!r}' for field in self._fields)})"
        )

    @meta_fn
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, type(self)):
            return False
        result: Num = Num._accept_(True)
        for field in self._fields:
            result = result.and_(field.__get__(self) == field.__get__(other))
        return result

    @meta_fn
    def __ne__(self, other: Any) -> bool:
        if not isinstance(other, type(self)):
            return True
        result: Num = Num._accept_(False)
        for field in self._fields:
            result = result.or_(field.__get__(self) != field.__get__(other))
        return result

    def __hash__(self):
        return hash(tuple(field.__get__(self) for field in self._fields))

    @meta_fn
    def __pos__(self) -> Self:
        """Return a copy of the record."""
        return self._copy_()

    @classmethod
    @meta_fn
    def type_var_value(cls, var: TypeVar, /) -> Any:
        """Return the value of a type variable.

        Args:
            var: The type variable to get the value of.

        Returns:
            The value of the type variable.
        """
        return super().type_var_value(var)


class _RecordField(SonolusDescriptor):
    def __init__(self, name: str, type_: type[Value], index: int, offset: int):
        self.name = name
        self.type = type_
        self.index = index
        self.offset = offset

    def __get__(self, instance: Record | None, owner=None):
        if instance is None:
            return self
        result = instance._value[self.name]._get_()
        if ctx():
            return result
        else:
            return result._as_py_()

    def __set__(self, instance: Record, value):
        value = self.type._accept_(value)
        if self.type._is_value_type_():
            instance._value[self.name]._set_(value)
        else:
            instance._value[self.name]._copy_from_(value)


_ops_to_inplace_ops = {
    "__add__": "__iadd__",
    "__sub__": "__isub__",
    "__mul__": "__imul__",
    "__truediv__": "__itruediv__",
    "__floordiv__": "__ifloordiv__",
    "__mod__": "__imod__",
    "__pow__": "__ipow__",
    "__lshift__": "__ilshift__",
    "__rshift__": "__irshift__",
    "__or__": "__ior__",
    "__xor__": "__ixor__",
    "__and__": "__iand__",
    "__matmul__": "__imatmul__",
}


def _add_inplace_ops(cls):
    for op, inplace_op in _ops_to_inplace_ops.items():
        if hasattr(cls, op) and not hasattr(cls, inplace_op):
            setattr(cls, inplace_op, _make_inplace_op(op))
    return cls


def _make_inplace_op(op: str):
    @meta_fn
    def inplace_op(self, other):
        _compiler_internal_ = True  # noqa: F841
        self._copy_from_(getattr(self, op)(other))
        return self

    return inplace_op
