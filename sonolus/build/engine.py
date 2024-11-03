import gzip
import json
import struct
from dataclasses import dataclass
from pathlib import Path

from sonolus.backend.mode import Mode
from sonolus.build.compile import compile_mode
from sonolus.script.archetype import BaseArchetype
from sonolus.script.bucket import Buckets
from sonolus.script.effect import Effects
from sonolus.script.engine import Engine
from sonolus.script.internal.context import ReadOnlyMemory
from sonolus.script.options import Options
from sonolus.script.particle import Particles
from sonolus.script.sprite import Skin
from sonolus.script.ui import UiConfig

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


@dataclass
class PackagedEngine:
    configuration: bytes
    play_data: bytes | None
    rom: bytes | None

    def write(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        if self.configuration:
            (path / "EngineConfiguration").write_bytes(self.configuration)
        if self.play_data:
            (path / "EnginePlayData").write_bytes(self.play_data)
        if self.rom:
            (path / "EngineRom").write_bytes(self.rom)


def build_engine(engine: Engine):
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
    return PackagedEngine(
        configuration=package_output(configuration),
        play_data=package_output(play_data),
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
    archetypes: list[type[BaseArchetype]],
    skin: Skin,
    effects: Effects,
    particles: Particles,
    buckets: Buckets,
    rom: ReadOnlyMemory,
):
    return {
        **compile_mode(mode=Mode.Play, rom=rom, archetypes=archetypes, global_callbacks=None),
        "skin": build_skin(skin),
        "effects": build_effects(effects),
        "particles": build_particles(particles),
        "buckets": build_buckets(buckets),
    }


def build_skin(skin: Skin) -> JsonValue:
    return {"sprites": [{"name": name, "id": i} for i, name in enumerate(skin._sprites_)]}


def build_effects(effects: Effects) -> JsonValue:
    return {"clips": [{"name": name, "id": i} for i, name in enumerate(effects._effects_)]}


def build_particles(particles: Particles) -> JsonValue:
    return {"particles": [{"name": name, "id": i} for i, name in enumerate(particles._particles_)]}


def build_buckets(buckets: Buckets) -> JsonValue:
    return {"buckets": [bucket.to_dict() for bucket in buckets._buckets_]}


def package_rom(rom: ReadOnlyMemory) -> bytes:
    values = rom.values
    output = bytearray()

    for value in values:
        output.extend(struct.pack("<f", value))

    return bytes(output)


def package_output(value: JsonValue) -> bytes:
    json_data = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return gzip.compress(json_data)
