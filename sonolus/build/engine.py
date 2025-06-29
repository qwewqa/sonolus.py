import gzip
import json
import struct
import sys
from collections.abc import Callable
from concurrent.futures import Executor
from concurrent.futures.thread import ThreadPoolExecutor
from dataclasses import dataclass
from os import process_cpu_count
from pathlib import Path

from sonolus.backend.mode import Mode
from sonolus.build.compile import compile_mode
from sonolus.script.archetype import _BaseArchetype
from sonolus.script.bucket import Buckets
from sonolus.script.effect import Effects
from sonolus.script.engine import EngineData, empty_play_mode, empty_preview_mode, empty_tutorial_mode, empty_watch_mode
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
from sonolus.script.project import BuildConfig
from sonolus.script.sprite import Skin
from sonolus.script.ui import UiConfig

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None


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

    def read(self, path: Path):
        self.configuration = (path / "EngineConfiguration").read_bytes()
        self.play_data = (path / "EnginePlayData").read_bytes()
        self.watch_data = (path / "EngineWatchData").read_bytes()
        self.preview_data = (path / "EnginePreviewData").read_bytes()
        self.tutorial_data = (path / "EngineTutorialData").read_bytes()
        self.rom = (path / "EngineRom").read_bytes()


def no_gil() -> bool:
    return sys.version_info >= (3, 13) and not sys._is_gil_enabled()


def package_engine(
    engine: EngineData,
    config: BuildConfig | None = None,
):
    config = config or BuildConfig()
    rom = ReadOnlyMemory()
    configuration = build_engine_configuration(engine.options, engine.ui)
    if no_gil():
        thread_pool = ThreadPoolExecutor(process_cpu_count() or 1)
    else:
        thread_pool = None

    play_mode = engine.play if config.build_play else empty_play_mode()
    watch_mode = engine.watch if config.build_watch else empty_watch_mode()
    preview_mode = engine.preview if config.build_preview else empty_preview_mode()
    tutorial_mode = engine.tutorial if config.build_tutorial else empty_tutorial_mode()

    if thread_pool is not None:
        futures = {
            "play": thread_pool.submit(
                build_play_mode,
                archetypes=play_mode.archetypes,
                skin=play_mode.skin,
                effects=play_mode.effects,
                particles=play_mode.particles,
                buckets=play_mode.buckets,
                rom=rom,
                config=config,
                thread_pool=thread_pool,
            ),
            "watch": thread_pool.submit(
                build_watch_mode,
                archetypes=watch_mode.archetypes,
                skin=watch_mode.skin,
                effects=watch_mode.effects,
                particles=watch_mode.particles,
                buckets=watch_mode.buckets,
                rom=rom,
                update_spawn=watch_mode.update_spawn,
                config=config,
                thread_pool=thread_pool,
            ),
            "preview": thread_pool.submit(
                build_preview_mode,
                archetypes=preview_mode.archetypes,
                skin=preview_mode.skin,
                rom=rom,
                config=config,
                thread_pool=thread_pool,
            ),
            "tutorial": thread_pool.submit(
                build_tutorial_mode,
                skin=tutorial_mode.skin,
                effects=tutorial_mode.effects,
                particles=tutorial_mode.particles,
                instructions=tutorial_mode.instructions,
                instruction_icons=tutorial_mode.instruction_icons,
                preprocess=tutorial_mode.preprocess,
                navigate=tutorial_mode.navigate,
                update=tutorial_mode.update,
                rom=rom,
                config=config,
                thread_pool=thread_pool,
            ),
        }

        play_data = futures["play"].result()
        watch_data = futures["watch"].result()
        preview_data = futures["preview"].result()
        tutorial_data = futures["tutorial"].result()
    else:
        play_data = build_play_mode(
            archetypes=play_mode.archetypes,
            skin=play_mode.skin,
            effects=play_mode.effects,
            particles=play_mode.particles,
            buckets=play_mode.buckets,
            rom=rom,
            config=config,
            thread_pool=None,
        )
        watch_data = build_watch_mode(
            archetypes=watch_mode.archetypes,
            skin=watch_mode.skin,
            effects=watch_mode.effects,
            particles=watch_mode.particles,
            buckets=watch_mode.buckets,
            rom=rom,
            update_spawn=watch_mode.update_spawn,
            config=config,
            thread_pool=None,
        )
        preview_data = build_preview_mode(
            archetypes=preview_mode.archetypes,
            skin=preview_mode.skin,
            rom=rom,
            config=config,
            thread_pool=None,
        )
        tutorial_data = build_tutorial_mode(
            skin=tutorial_mode.skin,
            effects=tutorial_mode.effects,
            particles=tutorial_mode.particles,
            instructions=tutorial_mode.instructions,
            instruction_icons=tutorial_mode.instruction_icons,
            preprocess=tutorial_mode.preprocess,
            navigate=tutorial_mode.navigate,
            update=tutorial_mode.update,
            rom=rom,
            config=config,
            thread_pool=None,
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
    config: BuildConfig,
    thread_pool: Executor | None = None,
):
    return {
        **compile_mode(
            mode=Mode.PLAY,
            rom=rom,
            archetypes=archetypes,
            global_callbacks=None,
            passes=config.passes,
            thread_pool=thread_pool,
        ),
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
    config: BuildConfig,
    thread_pool: Executor | None = None,
):
    return {
        **compile_mode(
            mode=Mode.WATCH,
            rom=rom,
            archetypes=archetypes,
            global_callbacks=[(update_spawn_callback, update_spawn)],
            passes=config.passes,
            thread_pool=thread_pool,
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
    config: BuildConfig,
    thread_pool: Executor | None = None,
):
    return {
        **compile_mode(
            mode=Mode.PREVIEW,
            rom=rom,
            archetypes=archetypes,
            global_callbacks=None,
            passes=config.passes,
            thread_pool=thread_pool,
        ),
        "skin": build_skin(skin),
    }


def build_tutorial_mode(
    skin: Skin,
    effects: Effects,
    particles: Particles,
    instructions: TutorialInstructions,
    instruction_icons: TutorialInstructionIcons,
    preprocess: Callable[[], None],
    navigate: Callable[[], None],
    update: Callable[[], None],
    rom: ReadOnlyMemory,
    config: BuildConfig,
    thread_pool: Executor | None = None,
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
            passes=config.passes,
            thread_pool=thread_pool,
        ),
        "skin": build_skin(skin),
        "effect": build_effects(effects),
        "particle": build_particles(particles),
        "instruction": build_instructions(instructions, instruction_icons),
    }


def build_skin(skin: Skin) -> JsonValue:
    return {
        "renderMode": skin.render_mode,
        "sprites": [{"name": name, "id": i} for i, name in enumerate(skin._sprites_)],
    }


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
