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
