"""FFI tests for the Rust engine-node interpreter (compiled extension required).

A handful of behaviors are checked differentially against the frozen legacy Python
interpreter (`sonolus.backend.interpret.Interpreter`), including error type and
message exactness. The exhaustive numeric-semantics edge tables live in the Rust
unit tests (`rust/sonolus-backend-core/tests/interpret.rs`); this file pins the
FFI surface: `EngineNodes` construction, `Interpreter` methods, exception mapping,
counters, and RNG modes.
"""

import math

import pytest

sonolus_backend = pytest.importorskip("sonolus_backend")

from sonolus.backend.interpret import Interpreter as LegacyInterpreter
from sonolus.backend.node import FunctionNode, format_engine_node
from sonolus.backend.ops import Op

# Node spec format (shared by both interpreters): a number (int -> int-tagged const,
# float -> float-tagged const) or an ("OpName", [args...]) pair.


def to_legacy(spec):
    if isinstance(spec, tuple):
        name, args = spec
        return FunctionNode(Op[name], tuple(to_legacy(arg) for arg in args))
    return spec


def run_both(spec, blocks=None):
    """Runs a spec on both interpreters and asserts identical observable behavior."""
    legacy = LegacyInterpreter()
    rust = sonolus_backend.Interpreter()
    for block_id, values in (blocks or {}).items():
        legacy.blocks[block_id] = list(values)
        rust.set_block(block_id, list(values))

    legacy_error = None
    legacy_result = None
    try:
        legacy_result = legacy.run(to_legacy(spec))
    except Exception as e:  # noqa: BLE001
        legacy_error = e

    rust_error = None
    rust_result = None
    try:
        rust_result = rust.run(sonolus_backend.EngineNodes(spec))
    except Exception as e:  # noqa: BLE001
        rust_error = e

    if legacy_error is not None:
        assert rust_error is not None, f"legacy raised {legacy_error!r}, Rust returned {rust_result!r}"
        assert type(rust_error) is type(legacy_error)
        assert str(rust_error) == str(legacy_error)
    else:
        assert rust_error is None, f"legacy returned {legacy_result!r}, Rust raised {rust_error!r}"
        if isinstance(legacy_result, float) and math.isnan(legacy_result):
            assert math.isnan(rust_result)
        else:
            assert rust_result == legacy_result
    assert rust.log == [float(entry) for entry in legacy.log]
    legacy_blocks = {block_id: [float(v) for v in values] for block_id, values in legacy.blocks.items()}
    rust_blocks = {block_id: rust.get_block(block_id) for block_id in rust.block_ids()}
    assert rust_blocks == legacy_blocks
    return rust_result


DIFFERENTIAL_SPECS = [
    # Arithmetic incl. floor-mod, banker's rounding, IEEE remainder, power folding.
    ("Add", [1, ("Multiply", [2.5, 3]), ("Negate", [4])]),
    ("Mod", [-5.5, 2]),
    ("Mod", [5.5, -2]),
    ("Round", [2.5]),
    ("Round", [-1.5]),
    ("Rem", [6, 4]),
    ("Power", [2, 3, 2]),
    ("Sign", [-0.0]),
    ("Frac", [-2.25]),
    ("Clamp", [2.0, 0.0, 1.0]),
    ("Lerp", [10.0, 20.0, 0.25]),
    ("Remap", [0.0, 10.0, 100.0, 200.0, 2.5]),
    # Errors: types and messages must match exactly.
    ("Divide", [1.0, 0.0]),
    ("Mod", [1.0, 0.0]),
    ("Power", [0.0, -1.0]),
    ("Log", [0.0]),
    ("Arccos", [2.0]),
    ("Round", [("Divide", [0.0, 0.0])]),
    ("Get", [5, -1]),
    ("Get", [5, 65536]),
    ("Get", [1.5, 0]),
    ("Copy", [0, 0, 1, 0, -1]),
    ("Ceil", [("Log", [("Execute", [])])]),
    # Control flow.
    ("If", [0, 10, 20]),
    ("If", [3, 10, 20]),
    ("And", [2, 3]),
    ("And", [2, 0, 5]),
    ("Or", [0, 0]),
    ("Or", [0, 7, 9]),
    ("Execute", []),
    ("Execute", [1, 2, 3]),
    ("Execute0", [1, 2]),
    ("Switch", [2.5, 1, 10, 2.5, 20]),
    ("Switch", [9, 1, 10]),
    ("SwitchWithDefault", [9, 1, 10, 99]),
    ("SwitchIntegerWithDefault", [1, 10, 20, 30, 99]),
    ("SwitchIntegerWithDefault", [1.5, 10, 20, 30, 99]),
    ("SwitchIntegerWithDefault", [-1, 10, 20, 30, 99]),
    ("SwitchInteger", [1, 10, 20, 30]),  # int scrutinee: works in both
    ("Block", [("Break", [1, 5])]),
    ("Block", [("Execute", [("Block", [("Break", [2, 42])]), 99])]),
    ("Block", [("Break", [0, 11])]),
    ("JumpLoop", [1, 2, 42]),
    ("JumpLoop", [-1, 42]),
    ("JumpLoop", [1.9, 42]),  # int() truncation toward zero selects the tail
]


@pytest.mark.parametrize("spec", DIFFERENTIAL_SPECS, ids=lambda spec: f"{spec[0]}{spec[1]}")
def test_differential_against_legacy(spec):
    run_both(spec)


def test_differential_memory_programs():
    # while get(0,0) < 5: set(0,0, get(0,0) + 1)
    run_both(
        (
            "While",
            [
                ("Less", [("Get", [0, 0]), 5]),
                ("Set", [0, 0, ("Add", [("Get", [0, 0]), 1])]),
            ],
        ),
        blocks={0: [0.0]},
    )
    # Memory defaults to -1.0 fill; negative block ids work.
    run_both(("Get", [-3, 4]))
    run_both(("Execute", [("Set", [-2, 3, 7.5]), ("Get", [-2, 1])]))
    # Overlapping copy reads everything before writing.
    run_both(("Copy", [1, 0, 1, 1, 2]), blocks={1: [1.0, 2.0, 3.0]})
    # Pointer-style accessors.
    run_both(("GetPointed", [1, 0, 1]), blocks={1: [3.0, 2.0], 3: [0.0, 0.0, 0.0, 9.0]})
    run_both(("SetPointed", [1, 0, 2, 5.0]), blocks={1: [3.0, 2.0]})
    run_both(("GetShifted", [3, 1, 1, 2]), blocks={3: [0.0, 1.0, 2.0, 3.0]})
    run_both(("IncrementPost", [0, 0]), blocks={0: [5.0]})
    run_both(("IncrementPre", [0, 0]))
    # DebugLog and DebugPause.
    run_both(("Execute", [("DebugLog", [1.5]), ("DebugPause", []), ("DebugLog", [-2.0])]))


def test_unsupported_op_matches_legacy():
    run_both(("Draw", []))


def test_legacy_assert_messages_via_ffi():
    interpreter = sonolus_backend.Interpreter()
    with pytest.raises(AssertionError, match="^Index must be non-negative$"):
        interpreter.get(0, -1)
    with pytest.raises(AssertionError, match="^Index is too large$"):
        interpreter.get(0, 65536)
    with pytest.raises(AssertionError, match="^Value must be an integer$"):
        interpreter.get(0.5, 0)
    with pytest.raises(AssertionError, match="^Count must be non-negative$"):
        interpreter.run(sonolus_backend.EngineNodes(("Copy", [0, 0, 1, 0, -1])))


def test_legacy_mutating_get_via_ffi():
    interpreter = sonolus_backend.Interpreter()
    assert interpreter.get(5, 3) == -1.0
    assert interpreter.get_block(5) == [-1.0, -1.0, -1.0, -1.0]
    assert interpreter.set(-2, 1, 9.0) == 9.0
    assert interpreter.get_block(-2) == [-1.0, 9.0]
    assert interpreter.block_ids() == [-2, 5]
    assert interpreter.get_block(99) is None


def test_counters():
    interpreter = sonolus_backend.Interpreter()
    assert interpreter.eval_count == 0
    assert interpreter.dispatch_count == 0
    # Add(1, 2): three node evaluations (consts count).
    interpreter.run(sonolus_backend.EngineNodes(("Add", [1, 2])))
    assert interpreter.eval_count == 3
    # JumpLoop 0 -> 1 -> tail: two non-tail dispatches, tail not counted; counters
    # accumulate across runs.
    interpreter.run(sonolus_backend.EngineNodes(("JumpLoop", [1, 2, 42])))
    assert interpreter.eval_count == 3 + 4
    assert interpreter.dispatch_count == 2


def test_seeded_rng_determinism():
    nodes = sonolus_backend.EngineNodes(("Random", [0, 1]))
    a = sonolus_backend.Interpreter(seed=123)
    b = sonolus_backend.Interpreter(seed=123)
    other = sonolus_backend.Interpreter(seed=124)
    seq_a = [a.run(nodes) for _ in range(8)]
    seq_b = [b.run(nodes) for _ in range(8)]
    seq_other = [other.run(nodes) for _ in range(8)]
    assert seq_a == seq_b
    assert seq_a != seq_other
    assert all(0.0 <= value < 1.0 for value in seq_a)
    int_nodes = sonolus_backend.EngineNodes(("RandomInteger", [-3, -1]))
    values = {sonolus_backend.Interpreter(seed=s).run(int_nodes) for s in range(32)}
    assert values == {-3.0, -2.0}


def test_rng_errors_match_legacy_messages():
    interpreter = sonolus_backend.Interpreter()
    with pytest.raises(ValueError, match=r"^empty range in randrange\(5, 5\)$"):
        interpreter.run(sonolus_backend.EngineNodes(("RandomInteger", [5, 5])))
    with pytest.raises(AssertionError, match="^Value must be an integer$"):
        interpreter.run(sonolus_backend.EngineNodes(("RandomInteger", [1.5, 3])))


def test_rng_tape_mode():
    interpreter = sonolus_backend.Interpreter(tape=[0.25, 7.0])
    assert interpreter.run(sonolus_backend.EngineNodes(("Random", [10, 20]))) == 0.25
    assert interpreter.run(sonolus_backend.EngineNodes(("RandomInteger", [0, 100]))) == 7.0
    with pytest.raises(RuntimeError, match="^RNG tape exhausted$"):
        interpreter.run(sonolus_backend.EngineNodes(("Random", [0, 1])))
    # set_rng_tape switches an existing interpreter into tape mode.
    interpreter = sonolus_backend.Interpreter(seed=1)
    interpreter.set_rng_tape([0.5])
    assert interpreter.run(sonolus_backend.EngineNodes(("Random", [0, 1]))) == 0.5


def test_uncaught_break_is_runtime_error():
    interpreter = sonolus_backend.Interpreter()
    with pytest.raises(RuntimeError, match="uncaught Break"):
        interpreter.run(sonolus_backend.EngineNodes(("Break", [1, 5])))


def test_format_matches_legacy_for_unambiguous_constants():
    # Values chosen so Python str() and Rust {:?} agree (decision D7 permits
    # divergence for e.g. nan/inf spellings).
    spec = ("Add", [1, ("Multiply", [2.5, 3]), ("Abs", [("Negate", [4])]), ("Execute", [])])
    nodes = sonolus_backend.EngineNodes(spec)
    assert nodes.format() == format_engine_node(to_legacy(spec))
    assert nodes.node_count() == 9


def test_engine_nodes_accepts_op_ids_and_lists():
    by_name = sonolus_backend.EngineNodes(("Add", [1, 2]))
    # Stable op ids are the 0-based definition order in ops.py (see gen_ops.py).
    add_id = list(Op).index(Op.Add)
    by_id = sonolus_backend.EngineNodes((add_id, [1, 2]))
    as_lists = sonolus_backend.EngineNodes(["Add", [1, 2]])
    for nodes in (by_name, by_id, as_lists):
        assert sonolus_backend.Interpreter().run(nodes) == 3.0

    with pytest.raises(ValueError, match="unknown op name"):
        sonolus_backend.EngineNodes(("NotAnOp", [1]))
    with pytest.raises(TypeError):
        sonolus_backend.EngineNodes(("Add", [1, "nope"]))
    with pytest.raises(TypeError):
        sonolus_backend.EngineNodes(None)


def test_deep_chain_through_ffi_is_iterative():
    # 100_000-deep nested Execute chain: both EngineNodes construction and run must
    # be iterative (the legacy interpreter would hit the recursion limit).
    depth = 100_000
    spec = 13.5
    for _ in range(depth):
        spec = ("Execute", [spec])
    interpreter = sonolus_backend.Interpreter()
    assert interpreter.run(sonolus_backend.EngineNodes(spec)) == 13.5
    assert interpreter.eval_count == depth + 1
