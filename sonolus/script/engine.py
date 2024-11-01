from typing import ClassVar

from sonolus.script.archetype import BaseArchetype
from sonolus.script.bucket import Buckets, EmptyBuckets
from sonolus.script.effect import Effects, EmptyEffects
from sonolus.script.options import EmptyOptions, Options
from sonolus.script.particle import EmptyParticles, Particles
from sonolus.script.sprite import EmptySkin, Skin
from sonolus.script.ui import UiConfig


class EngineMode:
    pass


class PlayMode(EngineMode):
    ui: UiConfig
    options: ClassVar[Options] = EmptyOptions

    archetypes: ClassVar[list[type[BaseArchetype]]]
    skin: ClassVar[Skin] = EmptySkin
    effects: ClassVar[Effects] = EmptyEffects
    particles: ClassVar[Particles] = EmptyParticles
    buckets: ClassVar[Buckets] = EmptyBuckets
