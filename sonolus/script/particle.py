from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, NewType, dataclass_transform, get_origin

from sonolus.backend.ops import Op
from sonolus.script.internal.introspection import get_field_specifiers
from sonolus.script.internal.native import native_function
from sonolus.script.quad import QuadLike, flatten_quad
from sonolus.script.record import Record


class Particle(Record):
    """A particle effect."""

    id: int

    def is_available(self) -> bool:
        """Check if the particle effect is available."""
        return _has_particle_effect(self.id)

    def spawn(self, quad: QuadLike, duration: float, loop: bool = False) -> ParticleHandle:
        """Spawn the particle effect.

        Args:
            quad: The quad to spawn the particle effect on.
            duration: The duration of the particle effect.
            loop: Whether to loop the particle effect.

        Returns:
            ParticleHandle: A handle to the spawned particle effect.
        """
        return ParticleHandle(_spawn_particle_effect(self.id, *flatten_quad(quad), duration, loop))


class ParticleHandle(Record):
    """A handle to a looping particle effect."""

    id: int

    def move(self, quad: QuadLike) -> None:
        """Move the particle effect to a new location.

        Args:
            quad: The new quad to move the particle effect to.
        """
        _move_particle_effect(self.id, *flatten_quad(quad))

    def destroy(self) -> None:
        """Destroy the particle effect."""
        _destroy_particle_effect(self.id)


@native_function(Op.HasParticleEffect)
def _has_particle_effect(particle_id: int) -> bool:
    raise NotImplementedError


@native_function(Op.SpawnParticleEffect)
def _spawn_particle_effect(
    particle_id: int,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
    x4: float,
    y4: float,
    duration: float,
    loop: bool,
) -> int:
    raise NotImplementedError


@native_function(Op.MoveParticleEffect)
def _move_particle_effect(
    handle: int, x1: float, y1: float, x2: float, y2: float, x3: float, y3: float, x4: float, y4: float
) -> None:
    raise NotImplementedError


@native_function(Op.DestroyParticleEffect)
def _destroy_particle_effect(handle: int) -> None:
    raise NotImplementedError


@dataclass
class _ParticleInfo:
    name: str


def particle(name: str) -> Any:
    """Define a particle with the given name."""
    return _ParticleInfo(name)


type Particles = NewType("Particles", Any)


@dataclass_transform()
def particles[T](cls: type[T]) -> T | Particles:
    """Decorator to define particles.

    Usage:
        ```python
        @particles
        class Particles:
            tap: StandardParticle.NOTE_CIRCULAR_TAP_RED
            other: Particle = particle("other")
        ```
    """
    if len(cls.__bases__) != 1:
        raise ValueError("Particles class must not inherit from any class (except object)")
    instance = cls()
    names = []
    for i, (name, annotation) in enumerate(get_field_specifiers(cls).items()):
        if get_origin(annotation) is not Annotated:
            raise TypeError(f"Invalid annotation for particles: {annotation}")
        annotation_type = annotation.__args__[0]
        annotation_values = annotation.__metadata__
        if annotation_type is not Particle:
            raise TypeError(f"Invalid annotation for particles: {annotation}, expected annotation of type Particle")
        if len(annotation_values) != 1 or not isinstance(annotation_values[0], _ParticleInfo):
            raise TypeError(
                f"Invalid annotation for particles: {annotation}, expected a single string annotation value"
            )
        particle_name = annotation_values[0].name
        names.append(particle_name)
        setattr(instance, name, Particle(i))
    instance._particles_ = names
    instance._is_comptime_value_ = True
    return instance


class StandardParticle:
    """Standard particles."""

    NOTE_CIRCULAR_TAP_NEUTRAL = Annotated[Particle, particle("#NOTE_CIRCULAR_TAP_NEUTRAL")]
    NOTE_CIRCULAR_TAP_RED = Annotated[Particle, particle("#NOTE_CIRCULAR_TAP_RED")]
    NOTE_CIRCULAR_TAP_GREEN = Annotated[Particle, particle("#NOTE_CIRCULAR_TAP_GREEN")]
    NOTE_CIRCULAR_TAP_BLUE = Annotated[Particle, particle("#NOTE_CIRCULAR_TAP_BLUE")]
    NOTE_CIRCULAR_TAP_YELLOW = Annotated[Particle, particle("#NOTE_CIRCULAR_TAP_YELLOW")]
    NOTE_CIRCULAR_TAP_PURPLE = Annotated[Particle, particle("#NOTE_CIRCULAR_TAP_PURPLE")]
    NOTE_CIRCULAR_TAP_CYAN = Annotated[Particle, particle("#NOTE_CIRCULAR_TAP_CYAN")]
    NOTE_CIRCULAR_ALTERNATIVE_NEUTRAL = Annotated[Particle, particle("#NOTE_CIRCULAR_ALTERNATIVE_NEUTRAL")]
    NOTE_CIRCULAR_ALTERNATIVE_RED = Annotated[Particle, particle("#NOTE_CIRCULAR_ALTERNATIVE_RED")]
    NOTE_CIRCULAR_ALTERNATIVE_GREEN = Annotated[Particle, particle("#NOTE_CIRCULAR_ALTERNATIVE_GREEN")]
    NOTE_CIRCULAR_ALTERNATIVE_BLUE = Annotated[Particle, particle("#NOTE_CIRCULAR_ALTERNATIVE_BLUE")]
    NOTE_CIRCULAR_ALTERNATIVE_YELLOW = Annotated[Particle, particle("#NOTE_CIRCULAR_ALTERNATIVE_YELLOW")]
    NOTE_CIRCULAR_ALTERNATIVE_PURPLE = Annotated[Particle, particle("#NOTE_CIRCULAR_ALTERNATIVE_PURPLE")]
    NOTE_CIRCULAR_ALTERNATIVE_CYAN = Annotated[Particle, particle("#NOTE_CIRCULAR_ALTERNATIVE_CYAN")]
    NOTE_CIRCULAR_HOLD_NEUTRAL = Annotated[Particle, particle("#NOTE_CIRCULAR_HOLD_NEUTRAL")]
    NOTE_CIRCULAR_HOLD_RED = Annotated[Particle, particle("#NOTE_CIRCULAR_HOLD_RED")]
    NOTE_CIRCULAR_HOLD_GREEN = Annotated[Particle, particle("#NOTE_CIRCULAR_HOLD_GREEN")]
    NOTE_CIRCULAR_HOLD_BLUE = Annotated[Particle, particle("#NOTE_CIRCULAR_HOLD_BLUE")]
    NOTE_CIRCULAR_HOLD_YELLOW = Annotated[Particle, particle("#NOTE_CIRCULAR_HOLD_YELLOW")]
    NOTE_CIRCULAR_HOLD_PURPLE = Annotated[Particle, particle("#NOTE_CIRCULAR_HOLD_PURPLE")]
    NOTE_CIRCULAR_HOLD_CYAN = Annotated[Particle, particle("#NOTE_CIRCULAR_HOLD_CYAN")]
    NOTE_LINEAR_TAP_NEUTRAL = Annotated[Particle, particle("#NOTE_LINEAR_TAP_NEUTRAL")]
    NOTE_LINEAR_TAP_RED = Annotated[Particle, particle("#NOTE_LINEAR_TAP_RED")]
    NOTE_LINEAR_TAP_GREEN = Annotated[Particle, particle("#NOTE_LINEAR_TAP_GREEN")]
    NOTE_LINEAR_TAP_BLUE = Annotated[Particle, particle("#NOTE_LINEAR_TAP_BLUE")]
    NOTE_LINEAR_TAP_YELLOW = Annotated[Particle, particle("#NOTE_LINEAR_TAP_YELLOW")]
    NOTE_LINEAR_TAP_PURPLE = Annotated[Particle, particle("#NOTE_LINEAR_TAP_PURPLE")]
    NOTE_LINEAR_TAP_CYAN = Annotated[Particle, particle("#NOTE_LINEAR_TAP_CYAN")]
    NOTE_LINEAR_ALTERNATIVE_NEUTRAL = Annotated[Particle, particle("#NOTE_LINEAR_ALTERNATIVE_NEUTRAL")]
    NOTE_LINEAR_ALTERNATIVE_RED = Annotated[Particle, particle("#NOTE_LINEAR_ALTERNATIVE_RED")]
    NOTE_LINEAR_ALTERNATIVE_GREEN = Annotated[Particle, particle("#NOTE_LINEAR_ALTERNATIVE_GREEN")]
    NOTE_LINEAR_ALTERNATIVE_BLUE = Annotated[Particle, particle("#NOTE_LINEAR_ALTERNATIVE_BLUE")]
    NOTE_LINEAR_ALTERNATIVE_YELLOW = Annotated[Particle, particle("#NOTE_LINEAR_ALTERNATIVE_YELLOW")]
    NOTE_LINEAR_ALTERNATIVE_PURPLE = Annotated[Particle, particle("#NOTE_LINEAR_ALTERNATIVE_PURPLE")]
    NOTE_LINEAR_ALTERNATIVE_CYAN = Annotated[Particle, particle("#NOTE_LINEAR_ALTERNATIVE_CYAN")]
    NOTE_LINEAR_HOLD_NEUTRAL = Annotated[Particle, particle("#NOTE_LINEAR_HOLD_NEUTRAL")]
    NOTE_LINEAR_HOLD_RED = Annotated[Particle, particle("#NOTE_LINEAR_HOLD_RED")]
    NOTE_LINEAR_HOLD_GREEN = Annotated[Particle, particle("#NOTE_LINEAR_HOLD_GREEN")]
    NOTE_LINEAR_HOLD_BLUE = Annotated[Particle, particle("#NOTE_LINEAR_HOLD_BLUE")]
    NOTE_LINEAR_HOLD_YELLOW = Annotated[Particle, particle("#NOTE_LINEAR_HOLD_YELLOW")]
    NOTE_LINEAR_HOLD_PURPLE = Annotated[Particle, particle("#NOTE_LINEAR_HOLD_PURPLE")]
    NOTE_LINEAR_HOLD_CYAN = Annotated[Particle, particle("#NOTE_LINEAR_HOLD_CYAN")]
    LANE_CIRCULAR = Annotated[Particle, particle("#LANE_CIRCULAR")]
    LANE_LINEAR = Annotated[Particle, particle("#LANE_LINEAR")]
    SLOT_CIRCULAR = Annotated[Particle, particle("#SLOT_CIRCULAR")]
    SLOT_LINEAR = Annotated[Particle, particle("#SLOT_LINEAR")]
    JUDGE_LINE_CIRCULAR = Annotated[Particle, particle("#JUDGE_LINE_CIRCULAR")]
    JUDGE_LINE_LINEAR = Annotated[Particle, particle("#JUDGE_LINE_LINEAR")]


@particles
class EmptyParticles:
    pass
