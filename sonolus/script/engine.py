from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sonolus.build.collection import Asset
from sonolus.script.archetype import PlayArchetype, PreviewArchetype, WatchArchetype, _BaseArchetype
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
    """A Sonolus.py engine.

    Args:
        name: The name of the engine.
        title: The title of the engine.
        subtitle: The subtitle of the engine.
        author: The author of the engine.
        skin: The default skin for the engine.
        background: The default background for the engine.
        effect: The default effect for the engine.
        particle: The default particle for the engine.
        thumbnail: The thumbnail for the engine.
        data: The engine's modes and configurations.
    """

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
    """A play mode definition.

    Args:
        archetypes: A list of play archetypes.
        skin: The skin for the play mode.
        effects: The effects for the play mode.
        particles: The particles for the play mode.
        buckets: The buckets for the play mode.
    """

    def __init__(
        self,
        *,
        archetypes: list[type[_BaseArchetype]] | None = None,
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
    """A watch mode definition.

    Args:
        archetypes: A list of watch archetypes.
        skin: The skin for the watch mode.
        effects: The effects for the watch mode.
        particles: The particles for the watch mode.
        buckets: The buckets for the watch mode.
        update_spawn: A callback returning the spawn time used by archetypes.
    """

    def __init__(
        self,
        *,
        archetypes: list[type[_BaseArchetype]] | None = None,
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
    """A preview mode definition.

    Args:
        archetypes: A list of preview archetypes.
        skin: The skin for the preview mode.
    """

    def __init__(
        self,
        *,
        archetypes: list[type[_BaseArchetype]] | None = None,
        skin: Skin = EmptySkin,
    ) -> None:
        self.archetypes = archetypes or []
        self.skin = skin

        for archetype in self.archetypes:
            if not issubclass(archetype, PreviewArchetype):
                raise ValueError(f"archetype {archetype} is not a BaseArchetype")


class TutorialMode:
    """A tutorial mode definition.

    Args:
        skin: The skin for the tutorial mode.
        effects: The effects for the tutorial mode.
        particles: The particles for the tutorial mode.
        instructions: The instructions for the tutorial mode.
        instruction_icons: The instruction icons for the tutorial mode.
        preprocess: A callback to be called before the tutorial starts.
        navigate: A callback to be called when the user navigates.
        update: A callback to be called each frame.
    """

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
    """A Sonolus.py engine's modes and configurations.

    Args:
        ui: The UI configuration.
        options: The options for the engine.
        play: The play mode configuration.
        watch: The watch mode configuration.
        preview: The preview mode configuration.
        tutorial: The tutorial mode configuration.
    """

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
