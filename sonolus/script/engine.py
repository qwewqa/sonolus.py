from __future__ import annotations

from collections.abc import Callable

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


def default_callback() -> float:
    return 0.0


class PlayMode:
    def __init__(
        self,
        *,
        archetypes: list[type[BaseArchetype]] | None = None,
        skin: Skin = EmptySkin,
        effects: Effects = EmptyEffects,
        particles: Particles = EmptyParticles,
        buckets: Buckets = EmptyBuckets,
    ) -> None:
        self.archetypes = archetypes or []
        self.skin = skin
        self.effects = effects
        self.particles = particles
        self.buckets = buckets


class WatchMode:
    def __init__(
        self,
        *,
        archetypes: list[type[BaseArchetype]] | None = None,
        skin: Skin = EmptySkin,
        effects: Effects = EmptyEffects,
        particles: Particles = EmptyParticles,
        buckets: Buckets = EmptyBuckets,
        update_spawn: Callable[[], float],
    ) -> None:
        self.archetypes = archetypes or []
        self.skin = skin
        self.effects = effects
        self.particles = particles
        self.buckets = buckets
        self.update_spawn = update_spawn


class EngineData:
    ui: UiConfig
    options: Options
    play: PlayMode
    watch: WatchMode

    def __init__(
        self,
        *,
        ui: UiConfig | None = None,
        options: Options = EmptyOptions,
        play: PlayMode | None = None,
        watch: WatchMode | None = None,
    ) -> None:
        self.ui = ui or UiConfig()
        self.options = options
        self.play = play or PlayMode()
        self.watch = watch or WatchMode(update_spawn=default_callback)
