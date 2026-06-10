# ruff: noqa: PLC2701
"""Live A/B tests for the Rust emitter + output-node generator (PORT.md T1.2).

For real traced callbacks across the frozen optimization levels, the legacy
pipeline (``cfg_to_engine_node`` + ``OutputNodeGenerator``) and the Rust pipeline
(encode post-pass CFG → ``cfg_to_engine_nodes`` → ``engine_nodes_to_output_dump``)
must produce byte-identical canonical output-node dumps. This pins dispatcher
selection, IR conversion, int/float tags, and DAG-dedup insertion order exactly.

The dump format (one line per output node) is defined by the Rust side
(``sonolus-backend-core::output::output_node_dump``) and replicated here:
``v i 0x<bits>`` / ``v f 0x<bits>`` for value nodes (raw IEEE-754 bits of the
value; tag from the Python value's type) and ``f <OpName> <arg indices...>`` for
function nodes.

Behavior checks additionally run both interpreters (legacy Python vs Rust via an
RNG tape recorded from the legacy run) and compare results and debug logs.

Skipped when the ``sonolus_backend`` extension is not installed.
"""

import math
import random
import struct
from contextlib import contextmanager

import pytest

sonolus_backend = pytest.importorskip("sonolus_backend")

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.encode import CfgEncodeError, encode_cfg
from sonolus.backend.finalize import cfg_to_engine_node
from sonolus.backend.interpret import Interpreter as LegacyInterpreter
from sonolus.backend.mode import Mode
from sonolus.backend.optimize.optimize import FAST_PASSES, MINIMAL_PASSES, STANDARD_PASSES
from sonolus.backend.optimize.passes import OptimizerConfig, run_passes
from sonolus.build.compile import callback_to_cfg
from sonolus.build.node import OutputNodeGenerator
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.visitor import compile_and_call
from tests.backend.test_cfg_roundtrip import REAL_CALLBACKS

LEVELS = [
    ("minimal", MINIMAL_PASSES),
    ("fast", FAST_PASSES),
    ("standard", STANDARD_PASSES),
]


def trace_cfg_with_rom(fn):
    @meta_fn
    def wrapper():
        return compile_and_call(fn)

    project_state = ProjectContextState(runtime_checks=RuntimeChecks.TERMINATE)
    mode_state = ModeContextState(Mode.PLAY)
    cfg = callback_to_cfg(project_state, mode_state, wrapper, "")
    return cfg, project_state.rom.values


def encode_post_cfg_or_skip(cfg, level: str) -> bytes:
    """Encodes a post-pass CFG, skipping the combos encoding v1 cannot represent.

    The standard pipeline can leave optimizer-internal constructs in places
    (IRExpr as ``BlockPlace.index``/``block``, e.g. an inlined ``Add`` index
    expression), which the frontend-level encoding deliberately rejects
    (rust/ENCODING.md section 7). The legacy emitter handles those live objects,
    but they cannot cross the encoding, so those A/B combos are skipped.
    Minimal/fast post-pass CFGs are always encodable (allocation produces
    concrete int indices); a skip there would be a real regression, so only
    standard-level place rejections are skipped.
    """
    try:
        return encode_cfg(cfg)
    except CfgEncodeError as e:
        if level == "standard" and "Unsupported BlockPlace" in str(e):
            pytest.skip(f"standard-level post-pass CFG not representable in encoding v1: {e}")
        raise


def python_output_dump(nodes: list[dict]) -> str:
    """Renders legacy output nodes in the Rust dump format (see the module docs)."""
    lines = []
    for node in nodes:
        if "value" in node:
            value = node["value"]
            tag = "i" if isinstance(value, int) else "f"
            bits = struct.unpack("<Q", struct.pack("<d", float(value)))[0]
            lines.append(f"v {tag} 0x{bits:016x}")
        else:
            lines.append(" ".join(["f", node["func"], *map(str, node["args"])]))
    return "\n".join(lines) + "\n"


def both_dumps(callback, passes, level) -> tuple[str, str]:
    cfg, _rom = trace_cfg_with_rom(callback)
    cfg = run_passes(cfg, passes, OptimizerConfig())
    # Encode FIRST: cfg_to_engine_node destroys the CFG (deletes block attrs).
    data = encode_post_cfg_or_skip(cfg, level)
    entry = cfg_to_engine_node(cfg)

    generator = OutputNodeGenerator()
    root = generator.add(entry)
    nodes = generator.get()
    assert root == len(nodes) - 1, "the root must be the last output node"
    python_dump = python_output_dump(nodes)

    rust_nodes = sonolus_backend.cfg_to_engine_nodes(data)
    rust_dump = sonolus_backend.engine_nodes_to_output_dump(rust_nodes)
    return python_dump, rust_dump


@pytest.mark.parametrize("level", [label for label, _ in LEVELS])
@pytest.mark.parametrize("callback", REAL_CALLBACKS, ids=lambda fn: fn.__name__)
def test_output_dumps_are_byte_identical(callback, level):
    passes = dict(LEVELS)[level]
    python_dump, rust_dump = both_dumps(callback, passes, level)
    assert rust_dump == python_dump


@contextmanager
def rng_tape_recorder(tape: list[float]):
    """Records the values drawn through random.uniform/randrange (legacy interpreter
    RNG), delegating to the originals so legacy behavior is unchanged."""
    orig_uniform = random.uniform
    orig_randrange = random.randrange

    def recording_uniform(a, b):
        value = orig_uniform(a, b)
        tape.append(value)
        return value

    def recording_randrange(a, b):
        value = orig_randrange(a, b)
        tape.append(float(value))
        return value

    random.uniform = recording_uniform
    random.randrange = recording_randrange
    try:
        yield
    finally:
        random.uniform = orig_uniform
        random.randrange = orig_randrange


def assert_values_equal(actual: float, expected: float, label: str):
    # Python `==` semantics (the behavioral-suite contract), made NaN-aware.
    if math.isnan(expected):
        assert math.isnan(actual), f"{label}: {actual!r} != {expected!r}"
    else:
        assert actual == expected, f"{label}: {actual!r} != {expected!r}"


@pytest.mark.parametrize("level", [label for label, _ in LEVELS])
@pytest.mark.parametrize("callback", REAL_CALLBACKS, ids=lambda fn: fn.__name__)
def test_rust_pipeline_behavior_matches_legacy(callback, level):
    passes = dict(LEVELS)[level]
    cfg, rom_values = trace_cfg_with_rom(callback)
    cfg = run_passes(cfg, passes, OptimizerConfig())
    data = encode_post_cfg_or_skip(cfg, level)  # before cfg_to_engine_node destroys the CFG
    entry = cfg_to_engine_node(cfg)

    legacy = LegacyInterpreter()
    legacy.blocks[int(PlayBlock.EngineRom)] = list(rom_values)
    tape: list[float] = []
    with rng_tape_recorder(tape):
        legacy_result = legacy.run(entry)

    rust_nodes = sonolus_backend.cfg_to_engine_nodes(data)
    rust = sonolus_backend.Interpreter(tape=tape)
    rust.set_block(int(PlayBlock.EngineRom), list(rom_values))
    rust_result = rust.run(rust_nodes)

    assert_values_equal(rust_result, legacy_result, "result")
    rust_log = rust.log
    assert len(rust_log) == len(legacy.log), "log length mismatch"
    for i, (actual, expected) in enumerate(zip(rust_log, legacy.log, strict=True)):
        assert_values_equal(actual, expected, f"log[{i}]")
