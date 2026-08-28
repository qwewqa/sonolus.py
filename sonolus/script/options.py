# ruff: file-ignore[builtin-argument-shadowing]
from dataclasses import dataclass
from typing import Annotated, Any, NewType, dataclass_transform, get_origin

from sonolus.backend.mode import Mode
from sonolus.backend.place import BlockPlace
from sonolus.script.debug import assert_unreachable
from sonolus.script.internal.context import ctx, debug_config
from sonolus.script.internal.descriptor import SonolusDescriptor
from sonolus.script.internal.generic import validate_concrete_type
from sonolus.script.internal.introspection import describe_value, get_field_specifiers
from sonolus.script.internal.simulation_context import sim_ctx
from sonolus.script.metadata import AnyText, encode_localization_text
from sonolus.script.num import Num
from sonolus.script.values import copy


@dataclass(frozen=True)
class OptionCategory:
    """A category of engine options.

    Args:
        name: The name of the category. If unset, the attribute name in the decorated options class is used.
        title: The display title of the category, as a plain string or an
            [`AnyText`][sonolus.script.metadata.AnyText] localization dict. If unset, the name is shown.
    """

    name: str | None = None
    title: AnyText | None = None

    def to_dict(self):
        if self.name is None:
            raise ValueError("Option category must be declared on an @options class or given an explicit name")
        return {
            "name": self.name,
            "title": encode_localization_text(self.name if self.title is None else self.title),
        }


def _option_category_name(category: str | OptionCategory | None) -> str | None:
    if isinstance(category, OptionCategory):
        if category.name is None:
            raise ValueError("Option category must be declared on an @options class or given an explicit name")
        return category.name
    return category


@dataclass
class _SliderOption:
    name: str | None
    category: str | OptionCategory | None
    title: str | None
    description: str | None
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
        if self.title is not None:
            result["title"] = self.title
        if (category := _option_category_name(self.category)) is not None:
            result["category"] = category
        if self.description is not None:
            result["description"] = self.description
        if self.scope is not None:
            result["scope"] = self.scope
        if self.unit is not None:
            result["unit"] = self.unit
        return result


@dataclass
class _ToggleOption:
    name: str | None
    category: str | OptionCategory | None
    title: str | None
    description: str | None
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
        if self.title is not None:
            result["title"] = self.title
        if (category := _option_category_name(self.category)) is not None:
            result["category"] = category
        if self.description is not None:
            result["description"] = self.description
        if self.scope is not None:
            result["scope"] = self.scope
        return result


@dataclass
class _SelectOption:
    name: str | None
    category: str | OptionCategory | None
    title: str | None
    description: str | None
    standard: bool
    advanced: bool
    scope: str | None
    default: int
    values: list[str]

    def to_dict(self):
        result = {
            "type": "select",
            "name": self.name,
            "standard": self.standard,
            "advanced": self.advanced,
            "def": self.default,
            "values": self.values,
        }
        if self.title is not None:
            result["title"] = self.title
        if (category := _option_category_name(self.category)) is not None:
            result["category"] = category
        if self.description is not None:
            result["description"] = self.description
        if self.scope is not None:
            result["scope"] = self.scope
        return result


def slider_option(
    *,
    name: str | None = None,
    category: str | OptionCategory | None = None,
    title: AnyText | None = None,
    description: AnyText | None = None,
    standard: bool = False,
    advanced: bool = False,
    default: float,
    min: float,
    max: float,
    step: float,
    unit: AnyText | None = None,
    scope: str | None = None,
) -> Any:
    """Define a slider option.

    Args:
        name: The name of the option.
        category: The category containing the option.
        title: The display title of the option, as a plain string or an
            [`AnyText`][sonolus.script.metadata.AnyText] localization dict. If unset, the name is shown.
        description: The description of the option, as a plain string or an
            [`AnyText`][sonolus.script.metadata.AnyText] localization dict.
        standard: Whether the option is standard.
        advanced: Whether the option is advanced.
        default: The default value of the option.
        min: The minimum value of the option.
        max: The maximum value of the option.
        step: The step value of the option.
        unit: The unit of the option, as a plain string or an
            [`AnyText`][sonolus.script.metadata.AnyText] localization dict.
        scope: The scope of the option.
    """
    return _SliderOption(
        name,
        category,
        encode_localization_text(title),
        encode_localization_text(description),
        standard,
        advanced,
        scope,
        default,
        min,
        max,
        step,
        encode_localization_text(unit),
    )


def toggle_option(
    *,
    name: str | None = None,
    category: str | OptionCategory | None = None,
    title: AnyText | None = None,
    description: AnyText | None = None,
    standard: bool = False,
    advanced: bool = False,
    default: bool,
    scope: str | None = None,
) -> Any:
    """Define a toggle option.

    Args:
        name: The name of the option.
        category: The category containing the option.
        title: The display title of the option, as a plain string or an
            [`AnyText`][sonolus.script.metadata.AnyText] localization dict. If unset, the name is shown.
        description: The description of the option, as a plain string or an
            [`AnyText`][sonolus.script.metadata.AnyText] localization dict.
        standard: Whether the option is standard.
        advanced: Whether the option is advanced.
        default: The default value of the option.
        scope: The scope of the option.
    """
    return _ToggleOption(
        name,
        category,
        encode_localization_text(title),
        encode_localization_text(description),
        standard,
        advanced,
        scope,
        default,
    )


def select_option(
    *,
    name: str | None = None,
    category: str | OptionCategory | None = None,
    title: AnyText | None = None,
    description: AnyText | None = None,
    standard: bool = False,
    advanced: bool = False,
    default: AnyText | int,
    values: list[AnyText],
    scope: str | None = None,
) -> Any:
    """Define a select option.

    Args:
        name: The name of the option.
        category: The category containing the option.
        title: The display title of the option, as a plain string or an
            [`AnyText`][sonolus.script.metadata.AnyText] localization dict. If unset, the name is shown.
        description: The description of the option, as a plain string or an
            [`AnyText`][sonolus.script.metadata.AnyText] localization dict.
        standard: Whether the option is standard.
        advanced: Whether the option is advanced.
        default: The default value of the option, given as an entry of `values` or an index into it.
        values: The values of the option, each a plain string or an
            [`AnyText`][sonolus.script.metadata.AnyText] localization dict.
        scope: The scope of the option.
    """
    if isinstance(default, bool):
        raise TypeError("Select option default index must be an integer, not bool")
    if isinstance(default, int):
        if not values:
            raise ValueError("Select option default index cannot be used with no values")
        if not 0 <= default < len(values):
            raise ValueError(f"Select option default index must be between 0 and {len(values) - 1}")
    else:
        default = values.index(default)
    return _SelectOption(
        name,
        category,
        encode_localization_text(title),
        encode_localization_text(description),
        standard,
        advanced,
        scope,
        default,
        [encode_localization_text(value) for value in values],
    )


type Options = NewType("Options", Any)  # type: ignore
type _OptionInfo = _SliderOption | _ToggleOption | _SelectOption


class _OptionField(SonolusDescriptor):
    info: _OptionInfo
    index: int

    def __init__(self, info: _OptionInfo, index: int):
        self.info = info
        self.index = index

    def __get__(self, instance, owner):
        if sim_ctx():
            return sim_ctx().get_or_put_value((instance, self), lambda: copy(self.info.default))
        if ctx():
            match ctx().mode_state.mode:
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
        raise RuntimeError("Options can only be accessed in a context")

    def __set__(self, instance, value):
        if sim_ctx():
            return sim_ctx().set_or_put_value((instance, self), lambda: copy(self.info.default), value)
        if ctx() and debug_config().unchecked_writes:
            match ctx().mode_state.mode:
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
                Num._from_place_(BlockPlace(block, self.index))._set_(Num._accept_(value))
                return
            else:
                raise RuntimeError("Options in the current mode cannot be set and use the default value")
        raise AttributeError("Options are read-only")


@dataclass_transform(kw_only_default=True)
def options[T](cls: type[T]) -> T | Options:
    """Decorator to define options.

    Note:
        A `replay_fallback_option_names` class attribute is excluded from the options list and is instead
        forwarded as `replayFallbackOptionNames` in the built engine configuration.

        If the class declares any [`OptionCategory`][sonolus.script.options.OptionCategory] attributes, every
        option must specify a category.

    Usage:
        ```python
        @options
        class Options:
            gameplay = OptionCategory(title='Gameplay')

            slider_option: float = slider_option(
                name='Slider Option',
                category=gameplay,
                standard=True,
                advanced=False,
                default=0.5,
                min=0,
                max=1,
                step=0.1,
                unit='unit',
                scope='scope',
            )
            toggle_option: bool = toggle_option(
                name='Toggle Option',
                category=gameplay,
                standard=True,
                advanced=False,
                default=True,
                scope='scope',
            )
            select_option: int = select_option(
                name='Select Option',
                category=gameplay,
                standard=True,
                advanced=False,
                default='value',
                values=['value'],
                scope='scope',
            )
        ```
    """
    if cls.__bases__ != (object,):
        raise ValueError("Options class must not inherit from any class (except object)")

    category_fields = set()
    categories = []
    category_members_by_id = {}
    category_values_by_id = {}
    category_members_by_name = {}
    category_members = []
    for member_name, category in vars(cls).items():
        if not isinstance(category, OptionCategory):
            continue
        category_fields.add(member_name)
        if previous_member := category_members_by_id.get(id(category)):
            raise ValueError(
                f"Option category fields {previous_member!r} and {member_name!r} reference the same object"
            )
        category_name = member_name if category.name is None else category.name
        category_title = category_name if category.title is None else category.title
        if previous_member := category_members_by_name.get(category_name):
            raise ValueError(
                f"Option category fields {previous_member!r} and {member_name!r} have the same name {category_name!r}"
            )
        resolved_category = OptionCategory(name=category_name, title=category_title)
        categories.append(resolved_category)
        category_members.append((member_name, resolved_category))
        category_members_by_id[id(category)] = member_name
        category_values_by_id[id(category)] = resolved_category
        category_members_by_name[category_name] = member_name

    for member_name, category in category_members:
        setattr(cls, member_name, category)

    instance = cls()
    entries = []
    for i, (name, annotation) in enumerate(
        get_field_specifiers(cls, skip=category_fields | {"replay_fallback_option_names"}).items()
    ):
        if get_origin(annotation) is not Annotated:
            raise TypeError(f"Invalid annotation for options: {describe_value(annotation)} on field {name}")
        annotation_type = annotation.__args__[0]
        annotation_values = annotation.__metadata__
        if len(annotation_values) != 1:
            raise ValueError(
                f"Invalid annotation values for options: {describe_value(annotation)} on field {name}, "
                f"expected a single annotation value"
            )
        try:
            annotation_type = validate_concrete_type(annotation_type)
        except TypeError as e:
            raise TypeError(f"Invalid annotation for options: {describe_value(annotation)} on field {name}: {e}") from e
        if annotation_type is not Num:
            raise TypeError(f"Invalid annotation type for options: {describe_value(annotation_type)} on field {name}")
        annotation_value = annotation_values[0]
        if not isinstance(annotation_value, _SliderOption | _ToggleOption | _SelectOption):
            raise TypeError(f"Invalid annotation value for options: {describe_value(annotation_value)} on field {name}")
        category = annotation_value.category
        if isinstance(category, OptionCategory):
            resolved_category = category_values_by_id.get(id(category))
            if resolved_category is None:
                raise ValueError(f"Option category on field {name} is not declared on the options class")
            category_name = resolved_category.name
        elif isinstance(category, str) or category is None:
            category_name = category
        else:
            raise TypeError(
                f"Invalid option category {describe_value(category)} on field {name}, expected OptionCategory or str"
            )
        if category_name is not None and category_name not in category_members_by_name:
            raise ValueError(f"Unknown option category {category_name!r} on field {name}")
        if categories and category_name is None:
            raise ValueError(
                f"Option on field {name} must specify a category when the options class declares categories"
            )
        annotation_value.category = category_name
        if annotation_value.name is None:
            annotation_value.name = name
        entries.append(annotation_value)
        setattr(cls, name, _OptionField(annotation_value, i))
    instance._option_categories = categories
    instance._options_ = entries
    instance._is_comptime_value_ = True
    return instance


@options
class EmptyOptions:
    """An option set with no options, used as the default when an engine declares none."""
