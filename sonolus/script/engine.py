from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sonolus.build.collection import Asset
from sonolus.script.archetype import BaseArchetype, PlayArchetype, PreviewArchetype, WatchArchetype
from sonolus.script.bucket import Buckets, EmptyBuckets
from sonolus.script.effect import Effects, EmptyEffects
from sonolus.script.instruction import (
    EmptyInstructionIcons,
    EmptyInstructions,
    TutorialInstructionIcons,
    TutorialInstructions,
)
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


def default_callback() -> Any:
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

        for archetype in self.archetypes:
            if not issubclass(archetype, PlayArchetype):
                raise ValueError(f"archetype {archetype} is not a PlayArchetype")


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

        for archetype in self.archetypes:
            if not issubclass(archetype, WatchArchetype):
                raise ValueError(f"archetype {archetype} is not a PlayArchetype")


class PreviewMode:
    def __init__(
        self,
        *,
        archetypes: list[type[BaseArchetype]] | None = None,
        skin: Skin = EmptySkin,
    ) -> None:
        self.archetypes = archetypes or []
        self.skin = skin

        for archetype in self.archetypes:
            if not issubclass(archetype, PreviewArchetype):
                raise ValueError(f"archetype {archetype} is not a BaseArchetype")


class TutorialMode:
    def __init__(
        self,
        *,
        skin: Skin = EmptySkin,
        effects: Effects = EmptyEffects,
        particles: Particles = EmptyParticles,
        instructions: TutorialInstructions = EmptyInstructions,
        instruction_icons: TutorialInstructionIcons = EmptyInstructionIcons,
        preprocess: Callable[[], None],
        navigate: Callable[[], None],
        update: Callable[[], None],
    ) -> None:
        self.skin = skin
        self.effects = effects
        self.particles = particles
        self.instructions = instructions
        self.instruction_icons = instruction_icons
        self.preprocess = preprocess
        self.navigate = navigate
        self.update = update


class EngineData:
    def __init__(
        self,
        *,
        ui: UiConfig | None = None,
        options: Options = EmptyOptions,
        play: PlayMode | None = None,
        watch: WatchMode | None = None,
        preview: PreviewMode | None = None,
        tutorial: TutorialMode | None = None,
    ) -> None:
        self.ui = ui or UiConfig()
        self.options = options
        self.play = play or PlayMode()
        self.watch = watch or WatchMode(update_spawn=default_callback)
        self.preview = preview or PreviewMode()
        self.tutorial = tutorial or TutorialMode(
            preprocess=default_callback,
            navigate=default_callback,
            update=default_callback,
        )
