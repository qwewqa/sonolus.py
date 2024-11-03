from __future__ import annotations

from typing import ClassVar

from sonolus.script.archetype import BaseArchetype
from sonolus.script.bucket import Buckets, EmptyBuckets
from sonolus.script.effect import Effects, EmptyEffects
from sonolus.script.options import Options
from sonolus.script.particle import EmptyParticles, Particles
from sonolus.script.sprite import EmptySkin, Skin
from sonolus.script.ui import UiConfig


class Engine:
    ui: UiConfig
    options: Options
    play: type[PlayMode]

    def __init__(
        self,
        ui: UiConfig,
        options: Options,
        *,
        play: type[PlayMode],
    ) -> None:
        self.ui = ui
        self.options = options
        self.play = play


class PlayMode:
    archetypes: ClassVar[list[type[BaseArchetype]]]
    skin: ClassVar[Skin] = EmptySkin
    effects: ClassVar[Effects] = EmptyEffects
    particles: ClassVar[Particles] = EmptyParticles
    buckets: ClassVar[Buckets] = EmptyBuckets
