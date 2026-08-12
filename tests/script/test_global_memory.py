import pytest

from sonolus.backend.mode import Mode
from sonolus.build.compile import callback_to_cfg
from sonolus.script.array import Array
from sonolus.script.globals import level_data, level_memory
from sonolus.script.internal.context import ModeContextState, ProjectContextState
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.visitor import clear_frontend_caches

# The generic level data / level memory blocks each hold at most this many values, per the
# Sonolus engine specification. See sonolus.backend.blocks.BLOCK_MEMORY_SIZES.
BLOCK_SIZE = 4096


def compile_in(mode: Mode, callback):
    clear_frontend_caches()
    project_state = ProjectContextState()
    mode_state = ModeContextState(mode)
    return callback_to_cfg(project_state, mode_state, callback, "preprocess")


def test_level_data_within_limit_compiles():
    data = level_data(Array[int, BLOCK_SIZE])

    def cb():
        data[0] = 1

    compile_in(Mode.PLAY, cb)  # should not raise


def test_level_data_overflow_raises():
    data = level_data(Array[int, BLOCK_SIZE + 1])

    def cb():
        data[0] = 1

    with pytest.raises(CompilationError, match=r"LevelData memory block exceeded its maximum size"):
        compile_in(Mode.PLAY, cb)


def test_level_memory_overflow_raises():
    memory = level_memory(Array[int, BLOCK_SIZE + 1])

    def cb():
        memory[0] = 1

    with pytest.raises(CompilationError, match=r"LevelMemory memory block exceeded its maximum size"):
        compile_in(Mode.PLAY, cb)


def test_overflow_accumulates_across_globals():
    a = level_memory(Array[int, BLOCK_SIZE - 1])
    b = level_memory(Array[int, 2])

    def cb():
        a[0] = 1
        b[0] = 2

    # Neither global alone exceeds the block, but together they do.
    with pytest.raises(CompilationError, match=r"LevelMemory memory block exceeded its maximum size"):
        compile_in(Mode.PLAY, cb)


def test_unused_overflowing_global_does_not_raise():
    # A declared-but-never-accessed global is never allocated, so it must not count.
    _unused = level_data(Array[int, BLOCK_SIZE + 100])
    used = level_data(Array[int, 8])

    def cb():
        used[0] = 1

    compile_in(Mode.PLAY, cb)  # should not raise


def test_level_data_overflow_raises_in_watch_mode():
    # Level data maps to the same generic block in watch mode; the limit applies there too.
    data = level_data(Array[int, BLOCK_SIZE + 1])

    def cb():
        _ = data[0]

    with pytest.raises(CompilationError, match=r"LevelData memory block exceeded its maximum size"):
        compile_in(Mode.WATCH, cb)


def test_level_memory_default_raises_at_decoration():
    with pytest.raises(TypeError, match=r"Default values are not supported for global fields: WithDefault\.x"):

        @level_memory
        class WithDefault:
            x: int = 5


def test_level_data_default_raises_at_decoration():
    with pytest.raises(TypeError, match=r"Default values are not supported for global fields: WithDefault\.x"):

        @level_data
        class WithDefault:
            x: int = 5


def test_level_memory_bad_annotation_names_the_class_and_field():
    with pytest.raises(TypeError, match=r"Invalid annotation for WithBad\.gamma") as exc_info:

        @level_memory
        class WithBad:
            alpha: int
            gamma: str
            delta: int

    assert "0x" not in str(exc_info.value)


def test_level_data_bad_annotation_names_the_class_and_field():
    with pytest.raises(TypeError, match=r"Invalid annotation for WithBad\.gamma"):

        @level_data
        class WithBad:
            alpha: int
            gamma: str


def test_level_memory_field_named_mro_without_default_works():
    # hasattr(cls, "mro") is True via the metaclass even though "mro" is never assigned in the class
    # body, so the default-rejection guard must check cls.__dict__ rather than hasattr.
    @level_memory
    class WithMro:
        mro: int

    def cb():
        WithMro.mro = 1

    compile_in(Mode.PLAY, cb)  # should not raise
