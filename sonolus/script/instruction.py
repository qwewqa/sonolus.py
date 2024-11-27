from dataclasses import dataclass
from typing import Annotated, Any, NewType, dataclass_transform, get_origin

from sonolus.backend.ops import Op
from sonolus.script.internal.introspection import get_field_specifiers
from sonolus.script.internal.native import native_function
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
        """Show this instruction text."""
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

        Args:
            position: The position of the icon.
            size: The size of the icon.
            rotation: The rotation of the icon.
            z: The z-index of the icon.
            a: The alpha of the icon.
        """
        _paint(self.id, position.x, position.y, size, rotation, z, a)


@dataclass
class _InstructionTextInfo:
    name: str


@dataclass
class _InstructionIconInfo:
    name: str


def instruction(name: str) -> Any:
    """Define an instruction with the given name."""
    return _InstructionTextInfo(name=name)


def instruction_icon(name: str) -> Any:
    """Define an instruction icon with the given name."""
    return _InstructionIconInfo(name=name)


type TutorialInstructions = NewType("TutorialInstructions", Any)
type TutorialInstructionIcons = NewType("TutorialInstructionIcons", Any)


@dataclass_transform()
def instructions[T](cls: type[T]) -> T | TutorialInstructions:
    """Decorator to define tutorial instructions.

    Usage:
        ```python
        @instructions
        class Instructions:
            tap: StandardInstruction.TAP
            other_instruction: InstructionText = instruction("Other Instruction")
        ```
    """
    if len(cls.__bases__) != 1:
        raise ValueError("Instructions class must not inherit from any class (except object)")
    instance = cls()
    names = []
    for i, (name, annotation) in enumerate(get_field_specifiers(cls).items()):
        if get_origin(annotation) is not Annotated:
            raise TypeError(f"Invalid annotation for instruction: {annotation}")
        annotation_type = annotation.__args__[0]
        annotation_values = annotation.__metadata__
        if annotation_type is not Instruction:
            raise TypeError(
                f"Invalid annotation for instruction: {annotation}, expected annotation of type InstructionText"
            )
        if len(annotation_values) != 1 or not isinstance(annotation_values[0], _InstructionTextInfo):
            raise TypeError(f"Invalid annotation for instruction: {annotation}, expected a single annotation value")
        instruction_name = annotation_values[0].name
        names.append(instruction_name)
        setattr(instance, name, Instruction(i))
    instance._instructions_ = names
    instance._is_comptime_value_ = True
    return instance


@dataclass_transform()
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
    if len(cls.__bases__) != 1:
        raise ValueError("Instruction icons class must not inherit from any class (except object)")
    instance = cls()
    names = []
    for i, (name, annotation) in enumerate(get_field_specifiers(cls).items()):
        if get_origin(annotation) is not Annotated:
            raise TypeError(f"Invalid annotation for instruction icon: {annotation}")
        annotation_type = annotation.__args__[0]
        annotation_values = annotation.__metadata__
        if annotation_type is not InstructionIcon:
            raise TypeError(
                f"Invalid annotation for instruction icon: {annotation}, expected annotation of type InstructionIcon"
            )
        if len(annotation_values) != 1 or not isinstance(annotation_values[0], _InstructionIconInfo):
            raise TypeError(
                f"Invalid annotation for instruction icon: {annotation}, expected a single annotation value"
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
    pass


@instruction_icons
class EmptyInstructionIcons:
    pass


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
    """Show the given instruction text."""
    _TutorialInstruction.text_id = inst.id


def clear_instruction():
    """Clear the current instruction text."""
    _TutorialInstruction.text_id = -1
