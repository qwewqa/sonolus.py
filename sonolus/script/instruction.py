from dataclasses import dataclass
from typing import Annotated, Any, NewType, dataclass_transform, get_origin

from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.script.internal.context import ctx
from sonolus.script.internal.introspection import describe_value, get_field_specifiers
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.native import native_function
from sonolus.script.metadata import AnyText, encode_localization_text
from sonolus.script.record import Record
from sonolus.script.runtime import _TutorialInstruction
from sonolus.script.text import StandardText
from sonolus.script.vec import Vec2


class Instruction(Record):
    """Tutorial instruction text.

    Usage:
        ```python
        Instruction(id: int)
        ```
    """

    id: int

    def show(self):
        """Show this instruction text.

        Only available in tutorial mode.
        """
        show_instruction(self)


class InstructionIcon(Record):
    """Tutorial instruction icon.

    Usage:
        ```python
        InstructionIcon(id: int)
        ```
    """

    id: int

    def paint(self, position: Vec2, size: float, rotation: float, z: float, a: float):
        """Paint this instruction icon.

        Only supported in tutorial mode.

        Args:
            position: The position of the icon.
            size: The size of the icon.
            rotation: The rotation of the icon, in degrees.
            z: The z-index of the icon.
            a: The alpha of the icon.
        """
        _check_paint_mode()
        _paint(self.id, position.x, position.y, size, rotation, z, a)


@dataclass
class _InstructionTextInfo:
    name: str


@dataclass
class _InstructionIconInfo:
    name: str


def instruction(name: AnyText) -> Any:
    """Define an instruction with the given name.

    Args:
        name: The instruction's text, as a plain string or an [`AnyText`][sonolus.script.metadata.AnyText]
            localization dict.
    """
    return _InstructionTextInfo(name=encode_localization_text(name))


def instruction_icon(name: str) -> Any:
    """Define an instruction icon with the given name."""
    return _InstructionIconInfo(name=name)


type TutorialInstructions = NewType("TutorialInstructions", Any)  # type: ignore
type TutorialInstructionIcons = NewType("TutorialInstructionIcons", Any)  # type: ignore


@dataclass_transform(kw_only_default=True)
def instructions[T](cls: type[T]) -> T | TutorialInstructions:
    """Decorator to define tutorial instructions.

    Usage:
        ```python
        @instructions
        class Instructions:
            tap: StandardInstruction.TAP
            other_instruction: Instruction = instruction("Other Instruction")
        ```
    """
    if cls.__bases__ != (object,):
        raise ValueError("Instructions class must not inherit from any class (except object)")
    instance = cls()
    names = []
    for i, (name, annotation) in enumerate(get_field_specifiers(cls).items()):
        described = describe_value(annotation)
        if get_origin(annotation) is not Annotated:
            raise TypeError(f"Invalid annotation for instruction: {described} on field {name}")
        annotation_type = annotation.__args__[0]
        annotation_values = annotation.__metadata__
        if annotation_type is not Instruction:
            raise TypeError(
                f"Invalid annotation for instruction: {described} on field {name}, "
                f"expected annotation of type Instruction"
            )
        if len(annotation_values) != 1 or not isinstance(annotation_values[0], _InstructionTextInfo):
            raise TypeError(
                f"Invalid annotation for instruction: {described} on field {name}, expected a single annotation value"
            )
        instruction_name = annotation_values[0].name
        names.append(instruction_name)
        setattr(instance, name, Instruction(i))
    instance._instructions_ = names
    instance._is_comptime_value_ = True
    return instance


@dataclass_transform(kw_only_default=True)
def instruction_icons[T](cls: type[T]) -> T | TutorialInstructionIcons:
    """Decorator to define tutorial instruction icons.

    Usage:
        ```python
        @instruction_icons
        class InstructionIcons:
            hand: StandardInstructionIcon.HAND
            other_icon: InstructionIcon = instruction_icon("Other Icon")
        ```
    """
    if cls.__bases__ != (object,):
        raise ValueError("Instruction icons class must not inherit from any class (except object)")
    instance = cls()
    names = []
    for i, (name, annotation) in enumerate(get_field_specifiers(cls).items()):
        described = describe_value(annotation)
        if get_origin(annotation) is not Annotated:
            raise TypeError(f"Invalid annotation for instruction icon: {described} on field {name}")
        annotation_type = annotation.__args__[0]
        annotation_values = annotation.__metadata__
        if annotation_type is not InstructionIcon:
            raise TypeError(
                f"Invalid annotation for instruction icon: {described} on field {name}, "
                f"expected annotation of type InstructionIcon"
            )
        if len(annotation_values) != 1 or not isinstance(annotation_values[0], _InstructionIconInfo):
            raise TypeError(
                f"Invalid annotation for instruction icon: {described} on field {name}, "
                f"expected a single annotation value"
            )
        icon_name = annotation_values[0].name
        names.append(icon_name)
        setattr(instance, name, InstructionIcon(i))
    instance._instruction_icons_ = names
    instance._is_comptime_value_ = True
    return instance


class StandardInstruction:
    """Standard instructions."""

    TAP = Annotated[Instruction, instruction(StandardText.TAP)]
    TAP_HOLD = Annotated[Instruction, instruction(StandardText.TAP_HOLD)]
    TAP_RELEASE = Annotated[Instruction, instruction(StandardText.TAP_RELEASE)]
    TAP_FLICK = Annotated[Instruction, instruction(StandardText.TAP_FLICK)]
    TAP_SLIDE = Annotated[Instruction, instruction(StandardText.TAP_SLIDE)]
    HOLD = Annotated[Instruction, instruction(StandardText.HOLD)]
    HOLD_SLIDE = Annotated[Instruction, instruction(StandardText.HOLD_SLIDE)]
    HOLD_FOLLOW = Annotated[Instruction, instruction(StandardText.HOLD_FOLLOW)]
    RELEASE = Annotated[Instruction, instruction(StandardText.RELEASE)]
    FLICK = Annotated[Instruction, instruction(StandardText.FLICK)]
    SLIDE = Annotated[Instruction, instruction(StandardText.SLIDE)]
    SLIDE_FLICK = Annotated[Instruction, instruction(StandardText.SLIDE_FLICK)]
    AVOID = Annotated[Instruction, instruction(StandardText.AVOID)]
    JIGGLE = Annotated[Instruction, instruction(StandardText.JIGGLE)]


class StandardInstructionIcon:
    """Standard instruction icons."""

    HAND = Annotated[InstructionIcon, instruction_icon("#HAND")]
    ARROW = Annotated[InstructionIcon, instruction_icon("#ARROW")]


@instructions
class EmptyInstructions:
    """An instruction set with no instructions, used as the default when a mode declares none."""


@instruction_icons
class EmptyInstructionIcons:
    """An instruction icon set with no icons, used as the default when a mode declares none."""


@meta_fn
def _check_paint_mode() -> None:
    if ctx() and ctx().mode_state.mode is not Mode.TUTORIAL:
        raise RuntimeError(
            f"InstructionIcon.paint is not available in '{ctx().mode_state.mode.name}' mode, only in TUTORIAL"
        )


@meta_fn
def _check_instruction_text_mode() -> None:
    if ctx() and ctx().mode_state.mode is not Mode.TUTORIAL:
        raise RuntimeError("Instruction text is only available in tutorial mode")


@native_function(Op.Paint)
def _paint(
    icon_id: int,
    x: float,
    y: float,
    size: float,
    rotation: float,
    z: float,
    a: float,
) -> None:
    raise NotImplementedError()


def show_instruction(inst: Instruction, /):
    """Show the given instruction text.

    Only available in tutorial mode.
    """
    _check_instruction_text_mode()
    _TutorialInstruction.text_id = inst.id


def clear_instruction():
    """Clear the current instruction text.

    Only available in tutorial mode.
    """
    _check_instruction_text_mode()
    _TutorialInstruction.text_id = -1
