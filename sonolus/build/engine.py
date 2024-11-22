import gzip
import json
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sonolus.backend.mode import Mode
from sonolus.build.compile import compile_mode
from sonolus.script.archetype import _BaseArchetype
from sonolus.script.bucket import Buckets
from sonolus.script.effect import Effects
from sonolus.script.engine import EngineData
from sonolus.script.instruction import (
    TutorialInstructionIcons,
    TutorialInstructions,
)
from sonolus.script.internal.callbacks import (
    navigate_callback,
    preprocess_callback,
    update_callback,
    update_spawn_callback,
)
from sonolus.script.internal.context import ReadOnlyMemory
from sonolus.script.options import Options
from sonolus.script.particle import Particles
from sonolus.script.sprite import Skin
from sonolus.script.ui import UiConfig

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


@dataclass
class PackagedEngine:
    configuration: bytes
    play_data: bytes
    watch_data: bytes
    preview_data: bytes
    tutorial_data: bytes
    rom: bytes

    def write(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        (path / "EngineConfiguration").write_bytes(self.configuration)
        (path / "EnginePlayData").write_bytes(self.play_data)
        (path / "EngineWatchData").write_bytes(self.watch_data)
        (path / "EnginePreviewData").write_bytes(self.preview_data)
        (path / "EngineTutorialData").write_bytes(self.tutorial_data)
        (path / "EngineRom").write_bytes(self.rom)


def package_engine(engine: EngineData):
    rom = ReadOnlyMemory()
    configuration = build_engine_configuration(engine.options, engine.ui)
    play_data = build_play_mode(
        archetypes=engine.play.archetypes,
        skin=engine.play.skin,
        effects=engine.play.effects,
        particles=engine.play.particles,
        buckets=engine.play.buckets,
        rom=rom,
    )
    watch_data = build_watch_mode(
        archetypes=engine.watch.archetypes,
        skin=engine.watch.skin,
        effects=engine.watch.effects,
        particles=engine.watch.particles,
        buckets=engine.watch.buckets,
        rom=rom,
        update_spawn=engine.watch.update_spawn,
    )
    preview_data = build_preview_mode(
        archetypes=engine.preview.archetypes,
        skin=engine.preview.skin,
        rom=rom,
    )
    tutorial_data = build_tutorial_mode(
        skin=engine.tutorial.skin,
        effects=engine.tutorial.effects,
        particles=engine.tutorial.particles,
        instructions=engine.tutorial.instructions,
        instruction_icons=engine.tutorial.instruction_icons,
        preprocess=engine.tutorial.preprocess,
        navigate=engine.tutorial.navigate,
        update=engine.tutorial.update,
        rom=rom,
    )
    return PackagedEngine(
        configuration=package_output(configuration),
        play_data=package_output(play_data),
        watch_data=package_output(watch_data),
        preview_data=package_output(preview_data),
        tutorial_data=package_output(tutorial_data),
        rom=package_rom(rom),
    )


def build_engine_configuration(
    options: Options,
    ui: UiConfig,
) -> JsonValue:
    return {
        "options": [option.to_dict() for option in options._options_],
        "ui": ui.to_dict(),
    }


def build_play_mode(
    archetypes: list[type[_BaseArchetype]],
    skin: Skin,
    effects: Effects,
    particles: Particles,
    buckets: Buckets,
    rom: ReadOnlyMemory,
):
    return {
        **compile_mode(mode=Mode.PLAY, rom=rom, archetypes=archetypes, global_callbacks=None),
        "skin": build_skin(skin),
        "effect": build_effects(effects),
        "particle": build_particles(particles),
        "buckets": build_buckets(buckets),
    }


def build_watch_mode(
    archetypes: list[type[_BaseArchetype]],
    skin: Skin,
    effects: Effects,
    particles: Particles,
    buckets: Buckets,
    rom: ReadOnlyMemory,
    update_spawn: Callable[[], float],
):
    return {
        **compile_mode(
            mode=Mode.WATCH, rom=rom, archetypes=archetypes, global_callbacks=[(update_spawn_callback, update_spawn)]
        ),
        "skin": build_skin(skin),
        "effect": build_effects(effects),
        "particle": build_particles(particles),
        "buckets": build_buckets(buckets),
    }


def build_preview_mode(
    archetypes: list[type[_BaseArchetype]],
    skin: Skin,
    rom: ReadOnlyMemory,
):
    return {
        **compile_mode(mode=Mode.PREVIEW, rom=rom, archetypes=archetypes, global_callbacks=None),
        "skin": build_skin(skin),
    }


def build_tutorial_mode(
    skin: Skin,
    effects: Effects,
    particles: Particles,
    instructions: TutorialInstructions,
    instruction_icons: TutorialInstructionIcons,
    preprocess: Callable[[], None],
    navigate: Callable[[int], None],
    update: Callable[[], None],
    rom: ReadOnlyMemory,
):
    return {
        **compile_mode(
            mode=Mode.TUTORIAL,
            rom=rom,
            archetypes=[],
            global_callbacks=[
                (preprocess_callback, preprocess),
                (navigate_callback, navigate),
                (update_callback, update),
            ],
        ),
        "skin": build_skin(skin),
        "effect": build_effects(effects),
        "particle": build_particles(particles),
        "instruction": build_instructions(instructions, instruction_icons),
    }


def build_skin(skin: Skin) -> JsonValue:
    return {"sprites": [{"name": name, "id": i} for i, name in enumerate(skin._sprites_)]}


def build_effects(effects: Effects) -> JsonValue:
    return {"clips": [{"name": name, "id": i} for i, name in enumerate(effects._effects_)]}


def build_particles(particles: Particles) -> JsonValue:
    return {"effects": [{"name": name, "id": i} for i, name in enumerate(particles._particles_)]}


def build_buckets(buckets: Buckets) -> JsonValue:
    return [bucket.to_dict() for bucket in buckets._buckets_]


def build_instructions(instructions: TutorialInstructions, instruction_icons: TutorialInstructionIcons) -> JsonValue:
    return {
        "texts": [{"name": name, "id": i} for i, name in enumerate(instructions._instructions_)],
        "icons": [{"name": name, "id": i} for i, name in enumerate(instruction_icons._instruction_icons_)],
    }


def package_rom(rom: ReadOnlyMemory) -> bytes:
    values = rom.values or [0]
    output = bytearray()

    for value in values:
        output.extend(struct.pack("<f", value))

    return gzip.compress(bytes(output))


def package_output(value: JsonValue) -> bytes:
    json_data = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return gzip.compress(json_data)
