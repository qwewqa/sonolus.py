from __future__ import annotations

import inspect
from collections.abc import Iterable
from inspect import getmro
from typing import Any, ClassVar, Self, dataclass_transform, get_origin, get_type_hints

from sonolus.backend.place import BlockPlace
from sonolus.script.internal.context import ctx
from sonolus.script.internal.generic import (
    GenericValue,
    accept_and_infer_types,
    validate_and_resolve_type,
    validate_concrete_type,
    validate_type_spec,
)
from sonolus.script.internal.impl import self_impl
from sonolus.script.internal.value import Value
from sonolus.script.num import Num


@dataclass_transform(eq_default=True)
class Record(GenericValue):
    _value: dict[str, Value]
    _fields: ClassVar[list[RecordField] | None] = None
    _constructor_signature: ClassVar[inspect.Signature]

    @classmethod
    def validate_type_args_(cls, args: tuple[Any, ...]) -> tuple[Any, ...]:
        if cls._fields is None:
            raise TypeError("Base Record class cannot have type arguments")
        return super().validate_type_args_(args)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()
        is_parameterizing = cls.type_args_ is not None and all(
            getattr(parent, "type_args_", None) is None for parent in getmro(cls)[1:]
        )
        if is_parameterizing:
            fields = []
            offset = 0
            for generic_field in cls._fields:
                resolved_type = validate_and_resolve_type(generic_field.type, cls.type_vars_to_args_)
                resolved_type = validate_concrete_type(resolved_type)
                field = RecordField(generic_field.name, resolved_type, generic_field.index, offset)
                fields.append(field)
                setattr(cls, field.name, field)
                offset += resolved_type.size_()
            cls._fields = fields
            return
        is_inheriting_from_existing_record_class = cls._fields is not None
        if is_inheriting_from_existing_record_class and not is_parameterizing:
            raise TypeError("Subclassing of a Record is not supported")

        hints = get_type_hints(cls)
        fields = []
        params = []
        index = 0
        for name, hint in hints.items():
            if name not in cls.__annotations__:
                continue
            if hint is ClassVar or get_origin(hint) is ClassVar:
                continue
            if hasattr(cls, name):
                raise TypeError("Default values are not supported for Record fields")
            type_ = validate_type_spec(hint)
            fields.append(RecordField(name, type_, index, len(fields)))
            index += 1
            params.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=type_,
                )
            )

        cls.parameterized_ = {}
        cls._fields = fields
        cls._constructor_signature = inspect.Signature(params)

        if len(getattr(cls, "__type_params__", ())) == 0:
            # Make the class behave as the parameterized version
            cls.type_args_ = ()
            cls.parameterized_[()] = cls

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
            values[field.name] = value.get_()
        for type_param in cls.__type_params__:
            if type_param not in type_vars:
                raise TypeError(f"Type parameter {type_param} is not used")
        type_args = tuple(type_vars[type_param] for type_param in cls.__type_params__)
        if cls.type_args_ is not None:
            if type_args != cls.type_args_:
                raise TypeError(
                    f"Invalid arguments for {cls.__name__}: expected type args {cls.type_args_}, got {type_args}"
                )
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
    def size_(cls) -> int:
        return sum(field.type.size_() for field in cls._fields)

    @classmethod
    def is_value_type_(cls) -> bool:
        return False

    @classmethod
    def from_place_(cls, place: BlockPlace) -> Self:
        return cls(**{field.name: field.type.from_place_(place.add_offset(field.offset)) for field in cls._fields})

    @classmethod
    def accepts_(cls, value: Any) -> bool:
        return issubclass(type(value), cls)

    @classmethod
    def accept_(cls, value: Any) -> Self:
        if not cls.accepts_(value):
            raise TypeError(f"Cannot accept value {value} as {cls.__name__}")
        return value

    def is_py_(self) -> bool:
        return all(value.is_py_() for value in self._value.values())

    def as_py_(self) -> Self:
        if not self.is_py_():
            raise ValueError("Not a python value")
        return self

    @classmethod
    def from_list_(cls, values: Iterable[float]) -> Self:
        iterator = iter(values)
        return cls(**{field.name: field.type.from_list_(iterator) for field in cls._fields})

    def to_list_(self) -> list[float]:
        result = []
        for field in self._fields:
            result.extend(field.type.to_list_(self._value[field.name]))
        return result

    def get_(self) -> Self:
        return self

    def set_(self, value: Self):
        raise TypeError("Record does not support set_")

    def copy_from_(self, value: Self):
        if not isinstance(value, type(self)):
            raise TypeError("Cannot copy from different type")
        for field in self._fields:
            field.__set__(self, field.__get__(value))

    def copy_(self) -> Self:
        return type(self)(**{field.name: self._value[field.name].copy_().get_() for field in self._fields})

    def __str__(self):
        return (
            f"{self.__class__.__name__}({", ".join(f"{field.name}={field.__get__(self)}" for field in self._fields)})"
        )

    @self_impl
    def __eq__(self, other):
        if not isinstance(other, type(self)):
            return False
        result: Num = Num.accept_(True)
        for field in self._fields:
            result = result.and_(field.__get__(self) == field.__get__(other))
        return result

    @self_impl
    def __ne__(self, other):
        if not isinstance(other, type(self)):
            return True
        result: Num = Num.accept_(False)
        for field in self._fields:
            result = result.or_(field.__get__(self) != field.__get__(other))
        return result

    def __hash__(self):
        raise TypeError("Record is not hashable")


class RecordField:
    def __init__(self, name: str, type_: type[Value], index: int, offset: int):
        self.name = name
        self.type = type_
        self.index = index
        self.offset = offset

    def __get__(self, instance: Record | None, owner=None):
        if instance is None:
            return self
        result = instance._value[self.name].get_()
        if ctx():
            return result
        else:
            return result.as_py_()

    def __set__(self, instance: Record, value):
        value = self.type.accept_(value)
        if self.type.is_value_type_():
            instance._value[self.name].set_(value)
        else:
            instance._value[self.name].copy_from_(value)
