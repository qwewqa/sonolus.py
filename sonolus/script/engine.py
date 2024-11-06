from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from sonolus.build.collection import Asset
from sonolus.script.archetype import BaseArchetype
from sonolus.script.bucket import Buckets, EmptyBuckets
from sonolus.script.effect import Effects, EmptyEffects
from sonolus.script.options import EmptyOptions, Options
from sonolus.script.particle import EmptyParticles, Particles
from sonolus.script.sprite import EmptySkin, Skin
from sonolus.script.ui import UiConfig


class Engine:
    version = 12

    def __init__(
        self,
        *,
        name: str,
        title: str | None = None,
        subtitle: str = "Sonolus.py Engine",
        author: str = "Unknown",
        skin: str | None = None,
        background: str | None = None,
        effect: str | None = None,
        particle: str | None = None,
        thumbnail: Asset | None = None,
        data: EngineData,
    ) -> None:
        self.name = name
        self.title = title or name
        self.subtitle = subtitle
        self.author = author
        self.skin = skin
        self.background = background
        self.effect = effect
        self.particle = particle
        self.thumbnail = thumbnail
        self.data = data


class PlayMode:
    archetypes: ClassVar[Sequence[type[BaseArchetype]]] = ()
    skin: ClassVar[Skin] = EmptySkin
    effects: ClassVar[Effects] = EmptyEffects
    particles: ClassVar[Particles] = EmptyParticles
    buckets: ClassVar[Buckets] = EmptyBuckets


class EngineData:
    ui: UiConfig
    options: Options
    play: type[PlayMode]

    def __init__(
        self,
        *,
        ui: UiConfig | None = None,
        options: Options = EmptyOptions,
        play: type[PlayMode] = PlayMode,
    ) -> None:
        self.ui = ui or UiConfig()
        self.options = options
        self.play = play
