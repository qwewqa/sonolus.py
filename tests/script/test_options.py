from dataclasses import FrozenInstanceError
from typing import Annotated

import pytest

from sonolus.backend.blocks import PlayBlock, PreviewBlock, WatchBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.mode import Mode
from sonolus.backend.optimize import STANDARD_PASSES, OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.build.compile import callback_to_cfg
from sonolus.script.bucket import Bucket, bucket, buckets
from sonolus.script.debug import debug_log
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
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks, enable_debug
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.simulation_context import SimulationContext
from sonolus.script.internal.visitor import clear_frontend_caches
from sonolus.script.num import Num
from sonolus.script.options import OptionCategory, options, select_option, slider_option, toggle_option
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


@pytest.mark.parametrize(
    "option",
    [
        slider_option(category=OptionCategory(name="gameplay"), default=0.5, min=0.0, max=1.0, step=0.1),
        toggle_option(category=OptionCategory(name="gameplay"), default=True),
        select_option(category=OptionCategory(name="gameplay"), default="a", values=["a", "b"]),
    ],
)
def test_option_category_object_is_serialized_by_name(option):
    assert option.to_dict()["category"] == "gameplay"


@pytest.mark.parametrize(
    "option",
    [
        slider_option(default=0.5, min=0.0, max=1.0, step=0.1),
        toggle_option(default=True),
        select_option(default="a", values=["a", "b"]),
    ],
)
def test_unset_option_category_is_omitted(option):
    assert "category" not in option.to_dict()


def test_options_rejects_unknown_category_name():
    with pytest.raises(ValueError, match="Unknown option category 'missing' on field toggle"):

        @options
        class Opts:
            gameplay = OptionCategory()
            toggle: bool = toggle_option(category="missing", default=True)


def test_options_accepts_inferred_category_name_string():
    @options
    class Opts:
        gameplay = OptionCategory()
        toggle: bool = toggle_option(category="gameplay", default=True)

    assert Opts.gameplay == OptionCategory(name="gameplay", title="gameplay")
    assert Opts._options_[0].to_dict()["category"] == "gameplay"


def test_options_rejects_undeclared_category_object():
    undeclared = OptionCategory(name="gameplay")

    with pytest.raises(ValueError, match="Option category on field toggle is not declared on the options class"):

        @options
        class Opts:
            toggle: bool = toggle_option(category=undeclared, default=True)


def test_options_rejects_duplicate_category_names():
    with pytest.raises(ValueError, match="fields 'first' and 'second' have the same name 'gameplay'"):

        @options
        class Opts:
            first = OptionCategory(name="gameplay")
            second = OptionCategory(name="gameplay")


def test_options_rejects_category_aliases():
    category = OptionCategory()

    with pytest.raises(ValueError, match="fields 'first' and 'second' reference the same object"):

        @options
        class Opts:
            first = category
            second = category


def test_options_resolves_reused_category_declaration_independently():
    category = OptionCategory()

    @options
    class FirstOpts:
        first = category
        toggle: bool = toggle_option(category=first, default=True)

    @options
    class SecondOpts:
        second = category
        toggle: bool = toggle_option(category=second, default=True)

    assert category == OptionCategory()
    assert FirstOpts.first == OptionCategory(name="first", title="first")
    assert SecondOpts.second == OptionCategory(name="second", title="second")
    assert FirstOpts._options_[0].to_dict()["category"] == "first"
    assert SecondOpts._options_[0].to_dict()["category"] == "second"

    with pytest.raises(FrozenInstanceError):
        FirstOpts.first.name = "renamed"


def test_options_rejects_invalid_category_type():
    with pytest.raises(TypeError, match=r"Invalid option category .* on field toggle, expected OptionCategory or str"):

        @options
        class Opts:
            toggle: bool = toggle_option(category=object(), default=True)


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


class _Plain:
    """A host object with the default repr, which is `<Cls object at 0xADDRESS>`."""


_PLAIN = _Plain()


def _decorate_with_annotation(decorator, annotation):
    return decorator(type("Bad", (), {"__annotations__": {"a": annotation}}))


@pytest.mark.parametrize(
    ("decorator", "message"),
    [
        (options, "Invalid annotation for options"),
        (effects, "Invalid annotation for effects"),
        (skin, "Invalid annotation for skin"),
        (particles, "Invalid annotation for particles"),
        (buckets, "Invalid annotation for buckets"),
        (instructions, "Invalid annotation for instruction"),
        (instruction_icons, "Invalid annotation for instruction icon"),
    ],
)
def test_decorator_rejecting_an_annotation_names_its_type(decorator, message):
    with pytest.raises(TypeError, match=message) as exc_info:
        _decorate_with_annotation(decorator, _PLAIN)

    text = str(exc_info.value)
    assert "_Plain" in text
    assert "0x" not in text
    assert "on field a" in text


@pytest.mark.parametrize(
    ("decorator", "annotation", "message"),
    [
        (options, Annotated[float, _PLAIN], "Invalid annotation value for options"),
        (effects, Annotated[Effect, _PLAIN], "unknown effect info"),
        (skin, Annotated[Sprite, _PLAIN], "unknown sprite info"),
        (particles, Annotated[Particle, _PLAIN], "unknown particle info"),
        (buckets, Annotated[Bucket, _PLAIN], "expected a single BucketInfo annotation value"),
        (
            instructions,
            Annotated[Instruction, _PLAIN],
            "Invalid annotation for instruction: .*expected a single annotation value",
        ),
        (
            instruction_icons,
            Annotated[InstructionIcon, _PLAIN],
            "Invalid annotation for instruction icon: .*expected a single annotation value",
        ),
    ],
)
def test_decorator_rejecting_an_annotation_value_names_its_type(decorator, annotation, message):
    # typing's own repr of an Annotated embeds the repr of each metadata value, so naming the type of the
    # annotation as a whole is not enough here: the address comes from inside it.
    with pytest.raises(TypeError, match=message) as exc_info:
        _decorate_with_annotation(decorator, annotation)

    text = str(exc_info.value)
    assert "_Plain" in text
    assert "0x" not in text
    assert "on field a" in text


def test_options_rejects_two_annotation_values_naming_the_field():
    annotation = Annotated[float, slider_option(default=0.5, min=0.0, max=1.0, step=0.1), toggle_option(default=True)]
    with pytest.raises(ValueError, match=r"Invalid annotation values for options: .* on field a, expected a single"):
        _decorate_with_annotation(options, annotation)


def test_options_rejects_a_non_num_annotation_type_naming_the_field():
    # Sprite is a supported concrete type, so this passes the annotation validation and has to be caught by
    # the options-are-numbers arm, unlike the `str` case below, which the validation itself rejects.
    with pytest.raises(TypeError, match=r"Invalid annotation type for options: Sprite on field a"):
        _decorate_with_annotation(options, Annotated[Sprite, toggle_option(default=True)])


def test_options_rejecting_a_non_value_annotation_names_its_field():
    # `str` is the likeliest wrong annotation here, since select_option's values and default are strings while
    # the option itself is a Num.
    with pytest.raises(TypeError, match="Invalid annotation for options") as exc_info:
        _decorate_with_annotation(options, Annotated[str, select_option(name="Bad", default="a", values=["a", "b"])])

    text = str(exc_info.value)
    assert "on field a" in text
    assert "str" in text


@options
class _ModeOpts:
    speed: float = slider_option(default=7.25, min=0.0, max=20.0, step=0.05)


# The option block each mode reads, named from sonolus/backend/blocks.py rather than from options.py. PLAY and
# WATCH resolve to the same block id, so a PLAY <-> WATCH swap is unobservable and these tests do not claim to
# catch one. Tutorial has no option block at all and is tested separately.
_MODE_OPTION_BLOCKS = [
    pytest.param(Mode.PLAY, PlayBlock.LevelOption, id="play"),
    pytest.param(Mode.WATCH, WatchBlock.LevelOption, id="watch"),
    pytest.param(Mode.PREVIEW, PreviewBlock.PreviewOption, id="preview"),
]

# A distinct value per option block, so a read from the wrong block returns the wrong number rather than nothing.
_SEEDED_OPTION_VALUES = {int(PlayBlock.LevelOption): 11.0, int(PreviewBlock.PreviewOption): 22.0}


def _interpret_in_mode(fn, mode: Mode) -> Interpreter:
    """Compile `fn` as a callback in `mode`, interpret it with both option blocks seeded, and return the oracle.

    An option read resolves to a block place and needs a compile context, so plain Python has no reference for it
    and `run_and_validate` cannot be the oracle. The returned interpreter carries the callback's log and the
    blocks it wrote.
    """
    clear_frontend_caches()
    project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
    cfg = callback_to_cfg(project_state, ModeContextState(mode), fn, "")
    entry = cfg_to_engine_node(run_passes(cfg, STANDARD_PASSES, OptimizerConfig(mode=mode)))
    interpreter = Interpreter()
    interpreter.blocks[int(mode.blocks.EngineRom)] = project_state.rom.values
    for block, value in _SEEDED_OPTION_VALUES.items():
        interpreter.set(block, 0, value)
    interpreter.run(entry)
    return interpreter


def _log_speed():
    debug_log(_ModeOpts.speed)


def _set_speed():
    _ModeOpts.speed = 3.0


@pytest.mark.parametrize(("mode", "block"), _MODE_OPTION_BLOCKS)
def test_option_read_comes_from_the_mode_option_block(mode, block):
    assert _interpret_in_mode(_log_speed, mode).log == [_SEEDED_OPTION_VALUES[int(block)]]


def test_option_read_in_tutorial_is_the_declared_default():
    # Tutorial has no option block, so the read is the default the option was declared with.
    assert _interpret_in_mode(_log_speed, Mode.TUTORIAL).log == [7.25]


@pytest.mark.parametrize(("mode", "block"), _MODE_OPTION_BLOCKS)
def test_option_unchecked_write_targets_the_mode_option_block(mode, block):
    with enable_debug():
        interpreter = _interpret_in_mode(_set_speed, mode)
    assert interpreter.get(int(block), 0) == 3.0
    for other, seeded in _SEEDED_OPTION_VALUES.items():
        if other != int(block):
            assert interpreter.get(other, 0) == seeded


def test_option_unchecked_write_in_tutorial_raises():
    with (
        enable_debug(),
        pytest.raises(CompilationError, match="Options in the current mode cannot be set and use the default value"),
    ):
        _interpret_in_mode(_set_speed, Mode.TUTORIAL)


def test_option_read_outside_a_context_raises():
    with pytest.raises(RuntimeError, match="Options can only be accessed in a context"):
        _ = _ModeOpts.speed


def test_option_in_a_simulation_context_holds_a_writable_value():
    # A simulation context answers before the mode mapping and before the read-only guard, so the option starts
    # at its declared default and takes a write without enable_debug().
    with SimulationContext():
        assert _ModeOpts.speed == 7.25
        _ModeOpts.speed = 3.0
        assert _ModeOpts.speed == 3.0
