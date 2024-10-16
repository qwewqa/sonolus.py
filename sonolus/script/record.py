from __future__ import annotations

import inspect
from inspect import getmro
from typing import dataclass_transform, ClassVar, Any, get_type_hints, get_origin

from sonolus.script.internal.generic import GenericValue, validate_type_spec, validate_and_resolve_type
from sonolus.script.internal.value import Value


@dataclass_transform(eq_default=True)
class Record(GenericValue):
    _value: dict[str, Value]
    _fields: ClassVar[list[RecordField] | None] = None
    _constructor_signature: ClassVar[inspect.Signature]

    @classmethod
    def validate_type_args_(cls, args: tuple[Any, ...]) -> tuple[Any, ...]:
        if cls._fields is None:
            raise TypeError(f"Base Record class cannot have type arguments")
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
                resolved_type = validate_type_spec(resolved_type)
                field = RecordField(generic_field.name, resolved_type, generic_field.index, offset)
                fields.append(field)
                setattr(cls, field.name, field)
                offset += resolved_type.size_()
            cls._fields = fields
        is_inheriting_from_existing_record_class = cls._fields is not None
        if is_inheriting_from_existing_record_class and not is_parameterizing:
            raise TypeError("Subclassing of a Record is not supported")

        hints = get_type_hints(cls)
        fields = []
        params = []
        for i, (name, hint) in enumerate(hints.items()):
            if name not in cls.__annotations__:
                continue
            if hint is ClassVar or get_origin(hint) is ClassVar:
                continue
            if hasattr(cls, name):
                raise TypeError(f"Default values are not supported for Record fields")
            type_ = validate_type_spec(hint)
            fields.append(RecordField(name, type_, i, len(fields)))
            params.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=type_,
                )
            )

        cls.parameterized_ = {}
        cls._fields = fields
        cls._constructor_signature_ = inspect.Signature(params)

        if len(getattr(cls, "__type_params__", ())) == 0:
            # Make the class behave as the parameterized version
            cls.type_args_ = ()
            cls.parameterized_[()] = cls

    # TODO


class RecordField:
    def __init__(self, name: str, type_: type[Value], index: int, offset: int):
        self.name = name
        self.type = type_
        self.index = index
        self.offset = offset

    def __get__(self, instance: Record, owner=None):
        if instance is None:
            return self
        return instance._value[self.name].get_()

    def __set__(self, instance: Record, value):
        value = self.type.accept_(value)
        if self.type.is_value_type_():
            instance._value[self.name].set_(value)
        else:
            instance._value[self.name].copy_from_(value)
