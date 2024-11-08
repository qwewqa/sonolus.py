# ruff: noqa: A002
from dataclasses import dataclass
from typing import Annotated, Any, NewType, dataclass_transform, get_origin

from sonolus.backend.mode import Mode
from sonolus.backend.place import BlockPlace
from sonolus.script.debug import assert_unreachable
from sonolus.script.internal.context import ctx
from sonolus.script.internal.descriptor import SonolusDescriptor
from sonolus.script.internal.generic import validate_concrete_type
from sonolus.script.internal.introspection import get_field_specifiers
from sonolus.script.num import Num


@dataclass
class SliderOption:
    name: str | None
    standard: bool
    advanced: bool
    scope: str | None
    default: float
    min: float
    max: float
    step: float
    unit: str | None

    def to_dict(self):
        result = {
            "type": "slider",
            "name": self.name,
            "standard": self.standard,
            "advanced": self.advanced,
            "def": self.default,
            "min": self.min,
            "max": self.max,
            "step": self.step,
        }
        if self.scope is not None:
            result["scope"] = self.scope
        if self.unit is not None:
            result["unit"] = self.unit
        return result


@dataclass
class ToggleOption:
    name: str | None
    standard: bool
    advanced: bool
    scope: str | None
    default: bool

    def to_dict(self):
        result = {
            "type": "toggle",
            "name": self.name,
            "standard": self.standard,
            "advanced": self.advanced,
            "def": int(self.default),
        }
        if self.scope is not None:
            result["scope"] = self.scope
        return result


@dataclass
class SelectOption:
    name: str | None
    standard: bool
    advanced: bool
    scope: str | None
    default: str
    values: list[str]

    def to_dict(self):
        result = {
            "type": "select",
            "name": self.name,
            "standard": self.standard,
            "advanced": self.advanced,
            "def": self.values.index(self.default),
            "values": self.values,
        }
        if self.scope is not None:
            result["scope"] = self.scope
        return result


def slider_option(
    *,
    name: str | None = None,
    standard: bool = False,
    advanced: bool = False,
    default: float,
    min: float,
    max: float,
    step: float,
    unit: str | None = None,
    scope: str | None = None,
) -> Any:
    return SliderOption(name, standard, advanced, scope, default, min, max, step, unit)


def toggle_option(
    *,
    name: str | None = None,
    standard: bool = False,
    advanced: bool = False,
    default: bool,
    scope: str | None = None,
) -> Any:
    return ToggleOption(name, standard, advanced, scope, default)


def select_option(
    *,
    name: str | None = None,
    standard: bool = False,
    advanced: bool = False,
    default: str,
    values: list[str],
    scope: str | None = None,
) -> Any:
    return SelectOption(name, standard, advanced, scope, default, values)


type Options = NewType("Options", Any)
type OptionInfo = SliderOption | ToggleOption | SelectOption


class OptionField(SonolusDescriptor):
    info: OptionInfo
    index: int

    def __init__(self, info: OptionInfo, index: int):
        self.info = info
        self.index = index

    def __get__(self, instance, owner):
        if ctx():
            match ctx().global_state.mode:
                case Mode.PLAY:
                    block = ctx().blocks.LevelOption
                case Mode.WATCH:
                    block = ctx().blocks.LevelOption
                case Mode.PREVIEW:
                    block = ctx().blocks.PreviewOption
                case Mode.TUTORIAL:
                    block = None
                case _:
                    assert_unreachable()
            if block is not None:
                return Num._from_place_(BlockPlace(block, self.index))
            else:
                return Num._accept_(self.info.default)

    def __set__(self, instance, value):
        raise AttributeError("Options are read-only")


@dataclass_transform()
def options[T](cls: type[T]) -> T | Options:
    if len(cls.__bases__) != 1:
        raise ValueError("Options class must not inherit from any class (except object)")
    instance = cls()
    entries = []
    for i, (name, annotation) in enumerate(get_field_specifiers(cls).items()):
        if get_origin(annotation) is not Annotated:
            raise TypeError(f"Invalid annotation for options: {annotation}")
        annotation_type = annotation.__args__[0]
        annotation_values = annotation.__metadata__
        if len(annotation_values) != 1:
            raise ValueError("Invalid annotation values for options")
        annotation_type = validate_concrete_type(annotation_type)
        if annotation_type is not Num:
            raise TypeError(f"Invalid annotation type for options: {annotation_type}")
        annotation_value = annotation_values[0]
        if not isinstance(annotation_value, SliderOption | ToggleOption | SelectOption):
            raise TypeError(f"Invalid annotation value for options: {annotation_value}")
        if annotation_value.name is None:
            annotation_value.name = name
        entries.append(annotation_value)
        setattr(cls, name, OptionField(annotation_value, i))
    instance._options_ = entries
    instance._is_comptime_value_ = True
    return instance


@options
class EmptyOptions:
    pass
