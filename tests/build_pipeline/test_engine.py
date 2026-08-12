"""Tests for sonolus.build.engine: packaged payload key names and the ROM/JSON byte encoding.

The determinism test (tests/regressions) compares two builds of the same code to each other, so it cannot catch
a key-name or byte-encoding regression: both sides would move identically and still agree. These tests instead
pin literal key names read off sonolus/build/engine.py and byte strings computed independently with the
documented struct/gzip formulas, never by calling the function under test a second time.
"""

import gzip
import json
import struct

import pytest

from sonolus.build import engine as engine_module
from sonolus.build.engine import (
    build_buckets,
    build_effects,
    build_engine_configuration,
    build_instructions,
    build_particles,
    build_skin,
    package_data,
    package_engine,
    package_rom,
    unpackage_data,
)
from sonolus.script.archetype import PlayArchetype, imported
from sonolus.script.bucket import Bucket, EmptyBuckets, bucket, bucket_sprite, buckets
from sonolus.script.effect import Effect, EmptyEffects, effect, effects
from sonolus.script.engine import EngineData, PlayMode, TutorialMode
from sonolus.script.instruction import (
    Instruction,
    InstructionIcon,
    instruction,
    instruction_icon,
    instruction_icons,
    instructions,
)
from sonolus.script.internal.context import ReadOnlyMemory
from sonolus.script.options import EmptyOptions, options, toggle_option
from sonolus.script.particle import EmptyParticles, Particle, particle, particles
from sonolus.script.project import BuildConfig
from sonolus.script.sprite import EmptySkin, RenderMode, Sprite, skin, sprite
from sonolus.script.ui import UiConfig


@skin
class _TwoSpriteSkin:
    render_mode: RenderMode = RenderMode.STANDARD
    a: Sprite = sprite("sprite_a")
    b: Sprite = sprite("sprite_b")


@effects
class _TwoEffectClips:
    a: Effect = effect("effect_a")
    b: Effect = effect("effect_b")


@particles
class _OneParticle:
    a: Particle = particle("particle_a")


@buckets
class _TwoBuckets:
    note: Bucket = bucket(sprites=[bucket_sprite(sprite=Sprite(0), x=0, y=0, w=1, h=1)])
    slide: Bucket = bucket(
        sprites=[bucket_sprite(sprite=Sprite(1), fallback_sprite=Sprite(3), x=0, y=0, w=2, h=2, rotation=90)],
        unit="ms",
    )


@instructions
class _OneInstruction:
    a: Instruction = instruction("Some Instruction")


@instruction_icons
class _OneInstructionIcon:
    a: InstructionIcon = instruction_icon("icon_a")


@options
class _OptionsWithFallback:
    replay_fallback_option_names = ("legacy_option",)
    toggle: bool = toggle_option(name="Toggle", default=True)


class _OneArchetype(PlayArchetype):
    name = "OneArchetype"
    beat: float = imported()

    def update_sequential(self):
        self.despawn = True


def _noop():
    pass


def test_build_skin_key_names_and_render_mode():
    result = build_skin(_TwoSpriteSkin)

    assert result == {
        "renderMode": "standard",
        "sprites": [
            {"name": "sprite_a", "id": 0},
            {"name": "sprite_b", "id": 1},
        ],
    }


def test_build_skin_empty():
    assert build_skin(EmptySkin) == {"renderMode": "default", "sprites": []}


def test_build_effects_uses_clips_key():
    # Deliberately asymmetric with build_particles: effects nests its list under "clips".
    result = build_effects(_TwoEffectClips)

    assert result == {
        "clips": [
            {"name": "effect_a", "id": 0},
            {"name": "effect_b", "id": 1},
        ]
    }


def test_build_effects_empty():
    assert build_effects(EmptyEffects) == {"clips": []}


def test_build_particles_uses_effects_key():
    # Deliberately asymmetric with build_effects: particles nests its list under "effects".
    result = build_particles(_OneParticle)

    assert result == {"effects": [{"name": "particle_a", "id": 0}]}


def test_build_particles_empty():
    assert build_particles(EmptyParticles) == {"effects": []}


def test_build_buckets_is_a_bare_list_not_a_dict():
    # Also covers the conditional "fallbackId" (on _BucketSprite) and "unit" (on _BucketInfo) keys,
    # which only appear when set, via the second bucket.
    result = build_buckets(_TwoBuckets)

    assert result == [
        {
            "sprites": [
                {"id": 0, "x": 0, "y": 0, "w": 1, "h": 1, "rotation": 0},
            ],
        },
        {
            "sprites": [
                {"id": 1, "x": 0, "y": 0, "w": 2, "h": 2, "rotation": 90, "fallbackId": 3},
            ],
            "unit": "ms",
        },
    ]


def test_build_buckets_empty():
    assert build_buckets(EmptyBuckets) == []


def test_build_instructions_key_names():
    result = build_instructions(_OneInstruction, _OneInstructionIcon)

    assert result == {
        "texts": [{"name": "Some Instruction", "id": 0}],
        "icons": [{"name": "icon_a", "id": 0}],
    }


def test_build_engine_configuration_wrapper_without_fallback():
    result = build_engine_configuration(EmptyOptions, UiConfig())

    assert result.keys() == {"options", "ui"}
    assert result["options"] == []
    assert result["ui"] == UiConfig().to_dict()


def test_build_engine_configuration_wrapper_with_fallback():
    result = build_engine_configuration(_OptionsWithFallback, UiConfig())

    assert result.keys() == {"options", "ui", "replayFallbackOptionNames"}
    assert result["options"] == [
        {"type": "toggle", "name": "Toggle", "standard": False, "advanced": False, "def": 1},
    ]
    assert result["replayFallbackOptionNames"] == ["legacy_option"]


def _stub_compile_mode(monkeypatch):
    # Isolates the {"skin": ..., "effect": ..., ...} wrapper keys that build_*_mode adds on top of
    # compile_mode's own dict, without running a real compile. The sentinel key proves the two dicts
    # are merged (spread), not one replacing the other.
    monkeypatch.setattr(engine_module, "compile_mode", lambda **kwargs: {"compiled": True})


def test_build_play_mode_wrapper_keys(monkeypatch):
    _stub_compile_mode(monkeypatch)

    result = engine_module.build_play_mode(
        archetypes=[],
        skin=EmptySkin,
        effects=EmptyEffects,
        particles=EmptyParticles,
        buckets=EmptyBuckets,
        project_state=None,
        config=BuildConfig(),
    )

    assert result.keys() == {"compiled", "skin", "effect", "particle", "buckets"}


def test_build_watch_mode_wrapper_keys(monkeypatch):
    _stub_compile_mode(monkeypatch)

    result = engine_module.build_watch_mode(
        archetypes=[],
        skin=EmptySkin,
        effects=EmptyEffects,
        particles=EmptyParticles,
        buckets=EmptyBuckets,
        project_state=None,
        update_spawn=lambda: 0.0,
        config=BuildConfig(),
    )

    assert result.keys() == {"compiled", "skin", "effect", "particle", "buckets"}


def test_build_preview_mode_wrapper_keys_omits_effect_particle_buckets(monkeypatch):
    _stub_compile_mode(monkeypatch)

    result = engine_module.build_preview_mode(
        archetypes=[],
        skin=EmptySkin,
        project_state=None,
        config=BuildConfig(),
    )

    assert result.keys() == {"compiled", "skin"}


def test_build_tutorial_mode_wrapper_keys_uses_instruction_not_buckets(monkeypatch):
    _stub_compile_mode(monkeypatch)

    result = engine_module.build_tutorial_mode(
        skin=EmptySkin,
        effects=EmptyEffects,
        particles=EmptyParticles,
        instructions=_OneInstruction,
        instruction_icons=_OneInstructionIcon,
        preprocess=lambda: None,
        navigate=lambda: None,
        update=lambda: None,
        project_state=None,
        config=BuildConfig(),
    )

    assert result.keys() == {"compiled", "skin", "effect", "particle", "instruction"}


def test_packaged_tutorial_payload_has_no_archetypes_section():
    # The EngineTutorialData spec declares no archetypes field, and a TutorialMode names no archetypes,
    # so the section must be absent rather than present and empty. Play is asserted from the same build
    # as the contrast: its spec does declare the field, so this pins the omission to tutorial alone.
    # The wrapper-key tests above stub compile_mode out, which is why none of them can see this.
    packaged = package_engine(
        EngineData(
            play=PlayMode(archetypes=[_OneArchetype]),
            tutorial=TutorialMode(preprocess=_noop, navigate=_noop, update=_noop),
        )
    )

    assert "archetypes" not in unpackage_data(packaged.tutorial_data)
    assert "archetypes" in unpackage_data(packaged.play_data)


def test_package_rom_encoding_is_little_endian_f32_then_gzip_mtime_zero():
    rom = ReadOnlyMemory()
    rom.values = [0.0, 1.5, -2.25]

    result = package_rom(rom)

    expected = gzip.compress(struct.pack("<3f", 0.0, 1.5, -2.25), mtime=0)
    assert result == expected


def test_package_rom_falls_back_to_a_single_zero_when_values_is_empty():
    rom = ReadOnlyMemory()
    rom.values = []

    result = package_rom(rom)

    assert result == gzip.compress(struct.pack("<f", 0.0), mtime=0)


def test_package_data_uses_compact_separators_and_gzip_mtime_zero():
    value = {"b": 1, "a": [1, 2, None, True, False]}

    result = package_data(value)

    expected_json = json.dumps(value, separators=(",", ":")).encode("utf-8")
    assert expected_json == b'{"b":1,"a":[1,2,null,true,false]}'
    assert result == gzip.compress(expected_json, mtime=0)


def test_package_data_unpackage_data_round_trip():
    value = {"skin": {"renderMode": "standard", "sprites": []}}

    assert unpackage_data(package_data(value)) == value


@buckets
class _BucketPastTheSkin:
    note: Bucket = bucket(sprites=[bucket_sprite(sprite=Sprite(2), x=0, y=0, w=1, h=1)])


@buckets
class _BucketWithFallbackPastTheSkin:
    note: Bucket = bucket(
        sprites=[bucket_sprite(sprite=Sprite(0), fallback_sprite=Sprite(3), x=0, y=0, w=1, h=1)],
    )


@buckets
class _BucketWithinTheSkin:
    note: Bucket = bucket(sprites=[bucket_sprite(sprite=Sprite(1), fallback_sprite=Sprite(0), x=0, y=0, w=1, h=1)])


MODE_BUILDERS = {
    "play": lambda **kwargs: engine_module.build_play_mode(
        archetypes=[],
        effects=EmptyEffects,
        particles=EmptyParticles,
        project_state=None,
        config=BuildConfig(),
        **kwargs,
    ),
    "watch": lambda **kwargs: engine_module.build_watch_mode(
        archetypes=[],
        effects=EmptyEffects,
        particles=EmptyParticles,
        project_state=None,
        update_spawn=lambda: 0.0,
        config=BuildConfig(),
        **kwargs,
    ),
}


@pytest.mark.parametrize("mode", sorted(MODE_BUILDERS))
def test_a_bucket_sprite_past_the_declared_skin_is_rejected(mode, monkeypatch):
    # A sprite id indexes the skin the same mode declares, so an id past its end can only render as the
    # missing-sprite fallback, and there is no runtime value involved for the check to be uncertain about.
    _stub_compile_mode(monkeypatch)

    with pytest.raises(ValueError, match="Bucket 0 references sprite id 2, but the skin of this mode declares 2"):
        MODE_BUILDERS[mode](skin=_TwoSpriteSkin, buckets=_BucketPastTheSkin)


@pytest.mark.parametrize("mode", sorted(MODE_BUILDERS))
def test_a_bucket_fallback_sprite_past_the_declared_skin_is_rejected(mode, monkeypatch):
    _stub_compile_mode(monkeypatch)

    with pytest.raises(ValueError, match="Bucket 0 references fallback sprite id 3"):
        MODE_BUILDERS[mode](skin=_TwoSpriteSkin, buckets=_BucketWithFallbackPastTheSkin)


@pytest.mark.parametrize("mode", sorted(MODE_BUILDERS))
def test_a_bucket_against_a_mode_that_declares_no_skin_is_rejected(mode, monkeypatch):
    # The likelier spelling of the same mistake: buckets are passed to the mode and the skin is left at its
    # default, so every bucket sprite is out of range.
    _stub_compile_mode(monkeypatch)

    with pytest.raises(ValueError, match="Bucket 0 references sprite id 1, but the skin of this mode declares 0"):
        MODE_BUILDERS[mode](skin=EmptySkin, buckets=_BucketWithinTheSkin)


@pytest.mark.parametrize("mode", sorted(MODE_BUILDERS))
def test_a_bucket_sprite_within_the_declared_skin_is_accepted(mode, monkeypatch):
    _stub_compile_mode(monkeypatch)

    result = MODE_BUILDERS[mode](skin=_TwoSpriteSkin, buckets=_BucketWithinTheSkin)

    assert result["buckets"] == build_buckets(_BucketWithinTheSkin)
