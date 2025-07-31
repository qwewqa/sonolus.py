# Particles

[Particle effects][sonolus.script.particle.Particle] are typically used for visuals such as the
effect when a note is hit. They are somewhat similar to sprites, but may contain their own animations
and support functionality such as looping. However, they come with the restriction that they do not have
their own z-indexes, and thus are always drawn on top of sprites while having no set order between themselves.

## Declaration

Particles are declared with the [`@particles`][sonolus.script.particle.particles] decorator. Standard Sonolus particles
are declared by using a value from [`StandardParticle`][sonolus.script.particle.StandardParticle] as the type hint.
Custom particles may also be defined using the [`Particle`][sonolus.script.particle.Particle] type hint and the
[`particle`][sonolus.script.particle.particle] function.

```python
from sonolus.script.particle import particles, particle, StandardParticle, Particle

@particles
class Particle:
    tap_note_hit_linear: StandardParticle.NOTE_LINEAR_TAP_CYAN
    tap_note_hit_circular: StandardParticle.NOTE_CIRCULAR_TAP_CYAN

    custom_particle: Particle = particle("name_of_custom_particle")
```

## Spawning a Particle

To spawn a particle, you can use the [`spawn`][sonolus.script.particle.Particle.spawn] method of the particle. This 
method accepts a [`Quad`][sonolus.script.quad.Quad] object that defines the position of the particle on the screen,
a duration in seconds determining how quick the particle animation should play, and optionally whether the particle
should loop:

```python
from sonolus.script.particle import Particle
from sonolus.script.quad import Quad

my_particle: Particle = ...
my_quad = Quad(
    tl=Vec2(-0.5, 0.5),
    tr=Vec2(0.5, 0.5),
    bl=Vec2(-0.5, -0.5),
    br=Vec2(0.5, -0.5),
)
handle = my_particle.spawn(my_quad, duration=0.5, loop=False)
```

The call to [`spawn`][sonolus.script.particle.Particle.spawn] will return a
[`ParticleHandle`][sonolus.script.particle.ParticleHandle] that can be used to control the particle after it has been 
spawned.

## Moving a Particle

A particle can be moved using the [`move`][sonolus.script.particle.ParticleHandle.move] method of the
[`ParticleHandle`][sonolus.script.particle.ParticleHandle]:

```python
from sonolus.script.particle import ParticleHandle
from sonolus.script.quad import Quad

my_particle_handle: ParticleHandle = ...
new_position: Quad = ...
my_particle_handle.move(new_position)
```

## Destroying a Particle

A particle can be destroyed using the [`destroy`][sonolus.script.particle.ParticleHandle.destroy] method of the
[`ParticleHandle`][sonolus.script.particle.ParticleHandle]:

```python
from sonolus.script.particle import ParticleHandle

my_particle_handle: ParticleHandle = ...
my_particle_handle.destroy()
```

## Checking Particle Availability

A particle effect may not be available depending on which effects a user has selected. To check if a particle is
available, you can use the [`is_available`][sonolus.script.particle.Particle.is_available] method of a 
[`Particle`][sonolus.script.particle.Particle]:

```python
from sonolus.script.particle import Particle

my_particle: Particle = ...
if my_particle.is_available():
    # The particle is available, you can spawn it.
    ...
else:
    # Do something else, such as using a fallback particle.
    ...
```
