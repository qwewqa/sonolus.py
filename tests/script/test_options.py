import pytest

from sonolus.script.bucket import Bucket, bucket, buckets
from sonolus.script.effect import Effect, effect, effects
from sonolus.script.globals import level_data, level_memory
from sonolus.script.instruction import (
    Instruction,
    InstructionIcon,
    instruction,
    instruction_icon,
    instruction_icons,
    instructions,
)
from sonolus.script.internal.context import enable_debug
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.num import Num
from sonolus.script.options import options, slider_option
from sonolus.script.particle import Particle, particle, particles
from sonolus.script.sprite import Sprite, skin, sprite
from sonolus.script.stream import Stream, StreamGroup, streams
from sonolus.script.vec import Vec2
from tests.script.conftest import compile_fn


@options
class _WriteOpts:
    foo: float = slider_option(default=0.5, min=0.0, max=1.0, step=0.1)


def test_option_unchecked_write_does_not_raise():
    @meta_fn
    def write():
        _WriteOpts.foo = 0.7

    # Non-debug default: options are read-only, so a write must still raise (regression guard).
    with pytest.raises(AttributeError, match="read-only"):
        compile_fn(write)

    # Debug (unchecked_writes=True): the write is emitted and must complete cleanly.
    # Before the fix, __set__ emitted the write and then unconditionally raised AttributeError.
    with enable_debug():
        compile_fn(write)


class _OptionsBase:
    inherited: float = slider_option(default=0.5, min=0.0, max=1.0, step=0.1)


class _EffectsBase:
    inherited: Effect = effect("inherited")


class _SkinBase:
    inherited: Sprite = sprite("inherited")


class _ParticlesBase:
    inherited: Particle = particle("inherited")


class _BucketsBase:
    inherited: Bucket = bucket(sprites=[])


class _InstructionsBase:
    inherited: Instruction = instruction("inherited")


class _InstructionIconsBase:
    inherited: InstructionIcon = instruction_icon("inherited")


class _StreamsBase:
    inherited: Stream[Vec2]


class _GlobalBase:
    inherited: int


@pytest.mark.parametrize(
    ("decorator", "base", "message"),
    [
        (options, _OptionsBase, "Options class must not inherit"),
        (effects, _EffectsBase, "Effects class must not inherit"),
        (skin, _SkinBase, "Skin class must not inherit"),
        (particles, _ParticlesBase, "Particles class must not inherit"),
        (buckets, _BucketsBase, "Buckets class must not inherit"),
        (instructions, _InstructionsBase, "Instructions class must not inherit"),
        (instruction_icons, _InstructionIconsBase, "Instruction icons class must not inherit"),
        (streams, _StreamsBase, "Streams class must not inherit"),
        (level_memory, _GlobalBase, "Expected a class with no bases"),
        (level_data, _GlobalBase, "Expected a class with no bases"),
    ],
)
def test_decorator_rejects_custom_base(decorator, base, message):
    with pytest.raises((ValueError, TypeError), match=message):
        decorator(type("Subclass", (base,), {}))


def test_options_accepts_plain_class():
    @options
    class Opts:
        foo: float = slider_option(default=0.5, min=0.0, max=1.0, step=0.1)
        bar: bool = slider_option(default=1.0, min=0.0, max=1.0, step=1.0)

    assert [entry.name for entry in Opts._options_] == ["foo", "bar"]


def test_other_decorators_accept_plain_classes():
    @effects
    class Effs:
        one: Effect = effect("one")

    @skin
    class Sk:
        one: Sprite = sprite("one")

    @particles
    class Parts:
        one: Particle = particle("one")

    @instructions
    class Ins:
        one: Instruction = instruction("one")

    @instruction_icons
    class Icons:
        one: InstructionIcon = instruction_icon("one")

    @level_data
    class Data:
        value: int

    assert Effs._effects_ == ["one"]
    assert Sk._sprites_ == ["one"]
    assert Parts._particles_ == ["one"]
    assert Ins._instructions_ == ["one"]
    assert Icons._instruction_icons_ == ["one"]
    assert type(Data)._global_info_.size == 1


def test_streams_data_field_with_builtin_annotation():
    @streams
    class Strms:
        stream: Stream[int]
        group: StreamGroup[int, 10]
        data_field: int
        vec_field: Vec2

    cls = type(Strms)
    cls._init_()

    assert cls._streams_ == [
        ("stream", 1, Stream[Num]),
        ("group", 2, StreamGroup[Num, 10]),
        ("data_field", 12, Num),
        ("vec_field", 13, Vec2),
    ]
    with pytest.raises(RuntimeError, match="only allowed in play and watch modes"):
        _ = Strms.data_field


def test_streams_rejects_unsupported_annotation():
    @streams
    class Strms:
        bad: str

    with pytest.raises(TypeError, match="Unsupported type spec"):
        type(Strms)._init_()


def test_streams_rejects_default_value():
    @streams
    class Strms:
        bad: int = 5

    with pytest.raises(TypeError, match="Default values are not supported"):
        type(Strms)._init_()
