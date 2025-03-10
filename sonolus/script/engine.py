from __future__ import annotations

import json
from collections.abc import Callable
from os import PathLike
from pathlib import Path
from typing import Any, Literal

from sonolus.build.collection import Asset, load_asset
from sonolus.script.archetype import PlayArchetype, PreviewArchetype, WatchArchetype, _BaseArchetype
from sonolus.script.bucket import Buckets, EmptyBuckets
from sonolus.script.effect import Effects, EmptyEffects
from sonolus.script.instruction import (
    EmptyInstructionIcons,
    EmptyInstructions,
    TutorialInstructionIcons,
    TutorialInstructions,
)
from sonolus.script.metadata import AnyText, Tag, as_localization_text
from sonolus.script.options import EmptyOptions, Options
from sonolus.script.particle import EmptyParticles, Particles
from sonolus.script.sprite import EmptySkin, Skin
from sonolus.script.ui import UiConfig


class ExportedEngine:
    """An exported Sonolus.py engine."""

    def __init__(
        self,
        *,
        item: dict,
        thumbnail: bytes,
        play_data: bytes,
        watch_data: bytes,
        preview_data: bytes,
        tutorial_data: bytes,
        rom: bytes | None = None,
        configuration: bytes,
    ) -> None:
        self.item = item
        self.thumbnail = thumbnail
        self.play_data = play_data
        self.watch_data = watch_data
        self.preview_data = preview_data
        self.tutorial_data = tutorial_data
        self.rom = rom
        self.configuration = configuration

    def write_to_dir(self, path: PathLike):
        """Write the exported engine to a directory."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        (path / "item.json").write_text(json.dumps(self.item, ensure_ascii=False), encoding="utf-8")
        (path / "thumbnail").write_bytes(self.thumbnail)
        (path / "playData").write_bytes(self.play_data)
        (path / "watchData").write_bytes(self.watch_data)
        (path / "previewData").write_bytes(self.preview_data)
        (path / "tutorialData").write_bytes(self.tutorial_data)
        if self.rom is not None:
            (path / "rom").write_bytes(self.rom)
        (path / "configuration").write_bytes(self.configuration)


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
        tags: The tags of the engine.
        description: The description of the engine.
        meta: Additional metadata of the engine.
    """

    version: Literal[13] = 13

    def __init__(
        self,
        *,
        name: str,
        title: AnyText | None = None,
        subtitle: AnyText = "Sonolus.py Engine",
        author: AnyText = "Unknown",
        skin: str | None = None,
        background: str | None = None,
        effect: str | None = None,
        particle: str | None = None,
        thumbnail: Asset | None = None,
        data: EngineData,
        tags: list[Tag] | None = None,
        description: AnyText | None = None,
        meta: Any = None,
    ) -> None:
        self.name = name
        self.title = as_localization_text(title or name)
        self.subtitle = as_localization_text(subtitle)
        self.author = as_localization_text(author)
        self.skin = skin
        self.background = background
        self.effect = effect
        self.particle = particle
        self.thumbnail = thumbnail
        self.data = data
        self.tags = tags or []
        self.description = as_localization_text(description) if description is not None else None
        self.meta = meta

    def export(self) -> ExportedEngine:
        """Export the engine in a sonolus-pack compatible format.

        Returns:
            An exported engine.
        """
        from sonolus.build.engine import package_engine
        from sonolus.build.project import BLANK_PNG

        item = {
            "version": self.version,
            "title": self.title,
            "subtitle": self.subtitle,
            "author": self.author,
            "tags": [tag.as_dict() for tag in self.tags],
            "skin": self.skin,
            "background": self.background,
            "effect": self.effect,
            "particle": self.particle,
        }
        packaged = package_engine(self.data)
        if self.description is not None:
            item["description"] = self.description
        if self.meta is not None:
            item["meta"] = self.meta
        return ExportedEngine(
            item=item,
            thumbnail=load_asset(self.thumbnail) if self.thumbnail is not None else BLANK_PNG,
            play_data=packaged.play_data,
            watch_data=packaged.watch_data,
            preview_data=packaged.preview_data,
            tutorial_data=packaged.tutorial_data,
            rom=packaged.rom,
            configuration=packaged.configuration,
        )


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


def empty_play_mode() -> PlayMode:
    """Create an empty play mode."""
    return PlayMode()


def empty_watch_mode() -> WatchMode:
    """Create an empty watch mode."""
    return WatchMode(update_spawn=default_callback)


def empty_preview_mode() -> PreviewMode:
    """Create an empty preview mode."""
    return PreviewMode()


def empty_tutorial_mode() -> TutorialMode:
    """Create an empty tutorial mode."""
    return TutorialMode(
        preprocess=default_callback,
        navigate=default_callback,
        update=default_callback,
    )


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
        self.play = play or empty_play_mode()
        self.watch = watch or empty_watch_mode()
        self.preview = preview or empty_preview_mode()
        self.tutorial = tutorial or empty_tutorial_mode()
