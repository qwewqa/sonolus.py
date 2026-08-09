"""Tests for the group forms of the skin, effects, and particles decorators."""

import random

import pytest

from sonolus.build.engine import build_effects, build_particles, build_skin
from sonolus.script.effect import Effect, EffectGroup, effect, effect_group, effects
from sonolus.script.internal.error import CompilationError
from sonolus.script.particle import Particle, ParticleGroup, particle, particle_group, particles
from sonolus.script.sprite import Sprite, SpriteGroup, skin, sprite, sprite_group
from tests.script.conftest import run_and_validate, run_compiled


@skin
class _Skin:
    first: Sprite = sprite("first")
    trio: SpriteGroup = sprite_group(["trio_0", "trio_1", "trio_2"])
    middle: Sprite = sprite("middle")
    empty: SpriteGroup = sprite_group([])
    pair: SpriteGroup = sprite_group(f"pair_{i}" for i in range(2))
    last: Sprite = sprite("last")


@effects
class _Effects:
    first: Effect = effect("first")
    trio: EffectGroup = effect_group(["trio_0", "trio_1", "trio_2"])
    middle: Effect = effect("middle")
    empty: EffectGroup = effect_group([])
    pair: EffectGroup = effect_group(f"pair_{i}" for i in range(2))
    last: Effect = effect("last")


@particles
class _Particles:
    first: Particle = particle("first")
    trio: ParticleGroup = particle_group(["trio_0", "trio_1", "trio_2"])
    middle: Particle = particle("middle")
    empty: ParticleGroup = particle_group([])
    pair: ParticleGroup = particle_group(f"pair_{i}" for i in range(2))
    last: Particle = particle("last")


# Each `trio` group above covers ids 1, 2, 3, which is what the shared expectations below are written against.
_GROUPS = [
    pytest.param(_Skin.trio, id="sprite"),
    pytest.param(_Effects.trio, id="effect"),
    pytest.param(_Particles.trio, id="particle"),
]

_READ_ONLY_GROUPS = [
    pytest.param(_Skin.trio, Sprite(9), "SpriteGroup is read-only", id="sprite"),
    pytest.param(_Effects.trio, Effect(9), "EffectGroup is read-only", id="effect"),
    pytest.param(_Particles.trio, Particle(9), "ParticleGroup is read-only", id="particle"),
]


def black_box_value(value: int) -> int:
    # Returns value unchanged, but through a branch the optimizer cannot fold, so an index built from it stays
    # runtime-valued and the bounds check is actually emitted instead of folded away.
    if random.randrange(0, 1) == 0:
        return value
    return 0


def test_skin_group_ids_agree_with_build_skin():
    # The two sides derive ids independently: the decorator hands each group a start id while walking the fields,
    # and build_skin re-numbers the flat name list it produced. They agree when the sprite a group yields at index
    # i carries the id the payload gave that group's i-th name. Expectations are read off the declarations above.
    assert _Skin.first.id == 0
    assert (_Skin.trio.start_id, _Skin.trio.size) == (1, 3)
    assert _Skin.trio[0].id == 1
    assert _Skin.trio[2].id == 3
    assert _Skin.middle.id == 4
    assert (_Skin.empty.start_id, _Skin.empty.size) == (5, 0)
    assert (_Skin.pair.start_id, _Skin.pair.size) == (5, 2)
    assert _Skin.pair[0].id == 5
    assert _Skin.pair[1].id == 6
    assert _Skin.last.id == 7
    assert build_skin(_Skin)["sprites"] == [
        {"name": "first", "id": 0},
        {"name": "trio_0", "id": 1},
        {"name": "trio_1", "id": 2},
        {"name": "trio_2", "id": 3},
        {"name": "middle", "id": 4},
        {"name": "pair_0", "id": 5},
        {"name": "pair_1", "id": 6},
        {"name": "last", "id": 7},
    ]


def test_effect_group_ids_agree_with_build_effects():
    assert _Effects.first.id == 0
    assert (_Effects.trio.start_id, _Effects.trio.size) == (1, 3)
    assert _Effects.trio[0].id == 1
    assert _Effects.trio[2].id == 3
    assert _Effects.middle.id == 4
    assert (_Effects.empty.start_id, _Effects.empty.size) == (5, 0)
    assert (_Effects.pair.start_id, _Effects.pair.size) == (5, 2)
    assert _Effects.pair[0].id == 5
    assert _Effects.pair[1].id == 6
    assert _Effects.last.id == 7
    assert build_effects(_Effects)["clips"] == [
        {"name": "first", "id": 0},
        {"name": "trio_0", "id": 1},
        {"name": "trio_1", "id": 2},
        {"name": "trio_2", "id": 3},
        {"name": "middle", "id": 4},
        {"name": "pair_0", "id": 5},
        {"name": "pair_1", "id": 6},
        {"name": "last", "id": 7},
    ]


def test_particle_group_ids_agree_with_build_particles():
    assert _Particles.first.id == 0
    assert (_Particles.trio.start_id, _Particles.trio.size) == (1, 3)
    assert _Particles.trio[0].id == 1
    assert _Particles.trio[2].id == 3
    assert _Particles.middle.id == 4
    assert (_Particles.empty.start_id, _Particles.empty.size) == (5, 0)
    assert (_Particles.pair.start_id, _Particles.pair.size) == (5, 2)
    assert _Particles.pair[0].id == 5
    assert _Particles.pair[1].id == 6
    assert _Particles.last.id == 7
    assert build_particles(_Particles)["effects"] == [
        {"name": "first", "id": 0},
        {"name": "trio_0", "id": 1},
        {"name": "trio_1", "id": 2},
        {"name": "trio_2", "id": 3},
        {"name": "middle", "id": 4},
        {"name": "pair_0", "id": 5},
        {"name": "pair_1", "id": 6},
        {"name": "last", "id": 7},
    ]


@pytest.mark.parametrize("group", _GROUPS)
def test_group_len(group):
    def fn():
        return len(group)

    assert run_and_validate(fn) == 3


@pytest.mark.parametrize("group", _GROUPS)
def test_group_getitem_with_constant_index(group):
    def fn():
        return group[0].id * 100 + group[1].id * 10 + group[2].id

    assert run_and_validate(fn) == 123


@pytest.mark.parametrize("group", _GROUPS)
def test_group_getitem_with_runtime_index(group):
    def fn():
        return group[black_box_value(0)].id * 100 + group[black_box_value(1)].id * 10 + group[black_box_value(2)].id

    assert run_and_validate(fn) == 123


@pytest.mark.parametrize("group", _GROUPS)
def test_group_getitem_rejects_index_past_end(group):
    def fn():
        return group[black_box_value(3)].id

    with pytest.raises(IndexError, match="Index out of range"):
        run_and_validate(fn)


@pytest.mark.parametrize("group", _GROUPS)
def test_group_getitem_rejects_negative_index(group):
    def fn():
        return group[black_box_value(-1)].id

    with pytest.raises(IndexError, match="Index out of range"):
        run_and_validate(fn)


@pytest.mark.parametrize("group", _GROUPS)
def test_group_get_unchecked(group):
    def fn():
        return (
            group.get_unchecked(black_box_value(0)).id * 100
            + group.get_unchecked(black_box_value(1)).id * 10
            + group.get_unchecked(black_box_value(2)).id
        )

    assert run_and_validate(fn) == 123


@pytest.mark.parametrize("group", _GROUPS)
def test_group_iteration(group):
    def fn():
        total = 0
        for element in group:
            total = total * 10 + element.id
        return total

    assert run_and_validate(fn) == 123


@pytest.mark.parametrize(("group", "element", "message"), _READ_ONLY_GROUPS)
def test_group_setitem_is_rejected(group, element, message):
    def fn():
        group[0] = element

    # The guard is a compile-time static_error, so there is nothing for run_and_validate to run: the assignment
    # never reaches the interpreter.
    with pytest.raises(CompilationError, match=message):
        run_compiled(fn)


def test_group_read_through_decorated_instance():
    def fn():
        return _Skin.trio[black_box_value(1)].id

    assert run_and_validate(fn) == 2
