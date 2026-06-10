# ruff: noqa: PLC2701
"""Round-trip tests: Python encoder vs Rust decoder (compiled extension required).

Validation is structural and bit-exact (decision D7): both sides render a canonical
dump in which all floats appear as raw IEEE-754 bits and int/float tags are explicit;
the dumps must be byte-identical. These tests are skipped when the `sonolus_backend`
extension is not installed (e.g. the tox lane without a Rust toolchain).
"""

import math
import struct

import pytest

sonolus_backend = pytest.importorskip("sonolus_backend")

from sonolus.backend.encode import cfg_canonical_dump, encode_cfg
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock
from sonolus.backend.place import BlockPlace, TempBlock
from sonolus.build.compile import callback_to_cfg
from sonolus.script.array import Array
from sonolus.script.debug import debug_log
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks, ctx
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.math_impls import _floor
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.random import _random
from sonolus.script.internal.visitor import compile_and_call
from sonolus.script.vec import Vec2


def assert_roundtrip(entry: BasicBlock) -> bytes:
    data = encode_cfg(entry)
    # Determinism: same CFG -> same bytes.
    assert encode_cfg(entry) == data
    # Structural bit-exact round trip: canonical dumps must be byte-identical.
    assert sonolus_backend.decode_cfg_canonical_dump(data) == cfg_canonical_dump(entry)
    # The debug dump must at least render without errors.
    assert sonolus_backend.decode_cfg_debug_dump(data)
    return data


# --- Real callbacks traced through the frontend ------------------------------------


def trace_cfg(fn, *args) -> BasicBlock:
    @meta_fn
    def wrapper():
        return compile_and_call(fn, *args)

    project_state = ProjectContextState(runtime_checks=RuntimeChecks.TERMINATE)
    mode_state = ModeContextState(Mode.PLAY)
    return callback_to_cfg(project_state, mode_state, wrapper, "")


@meta_fn
def blur(x):
    """Makes a compile-time number opaque to the compiler (mirrors tests/script usage)."""
    if not ctx():
        return x
    return validate_value(x) + _floor(_random())


def arithmetic_callback():
    total = 0.0
    for i in range(10):
        total = total + i * 1.5 - total / 2
    return total


def array_dynamic_index_callback():
    values = Array[int, 8](1, 2, 3, 4, 5, 6, 7, 8)
    total = 0
    for i in range(blur(0), 8):
        values[i] += 1
        total += values[i]
    return total


def float_switch_callback():
    cases = {1.5: 10, 2.5: 20, -3.0: 30}
    return cases[blur(2.5)]


def int_match_callback():
    x = blur(3)
    match x:
        case 1:
            return 10
        case 2:
            return 20
        case 3:
            return 30
        case _:
            return -1


def while_break_callback():
    n = blur(17)
    count = 0
    while n > 1:
        if n % 2 == 0:
            n //= 2
        else:
            n = 3 * n + 1
        count += 1
        if count > 100:
            break
    return count


def vec_callback():
    v = Vec2(blur(3.0), blur(4.0))
    w = v + Vec2(1.0, -1.0)
    return w.magnitude + v.dot(w)


def debug_log_callback():
    x = blur(5)
    debug_log(x)
    debug_log(x * 2.5)
    return x


REAL_CALLBACKS = [
    arithmetic_callback,
    array_dynamic_index_callback,
    float_switch_callback,
    int_match_callback,
    while_break_callback,
    vec_callback,
    debug_log_callback,
]


@pytest.mark.parametrize("callback", REAL_CALLBACKS, ids=lambda fn: fn.__name__)
def test_roundtrip_traced_callback(callback):
    assert_roundtrip(trace_cfg(callback))


def test_traced_encoding_is_deterministic_across_traces():
    first = encode_cfg(trace_cfg(arithmetic_callback))
    second = encode_cfg(trace_cfg(arithmetic_callback))
    assert first == second


# --- Hand-built edge cases ----------------------------------------------------------


def test_roundtrip_empty_block():
    assert_roundtrip(BasicBlock())


def test_roundtrip_deep_expression_nesting():
    # Far beyond Python's default recursion limit: proves encoder and dumps are iterative.
    expr = IRConst(1)
    for _ in range(5000):
        expr = IRPureInstr(Op.Negate, [expr])
    cfg = BasicBlock(statements=[IRSet(BlockPlace(TempBlock("v0", 1), 0), expr)])
    assert_roundtrip(cfg)


def test_roundtrip_deeply_nested_places():
    place = BlockPlace(4000, 0)
    for i in range(3000):
        place = BlockPlace(block=place, index=BlockPlace(TempBlock(f"idx{i % 7}", 1), 0), offset=i % 3)
    cfg = BasicBlock(statements=[IRGet(place)])
    assert_roundtrip(cfg)


def test_roundtrip_long_block_chain():
    # Thousands of sequential blocks: proves the RPO traversal is iterative.
    entry = BasicBlock()
    current = entry
    for i in range(3000):
        nxt = BasicBlock(statements=[IRSet(BlockPlace(TempBlock("v0", 1), 0), IRConst(i))])
        current.connect_to(nxt)
        current = nxt
    assert_roundtrip(entry)


def test_roundtrip_dynamic_block_and_index():
    place = BlockPlace(
        block=BlockPlace(4002, 1),
        index=BlockPlace(TempBlock("idx", 1), 0),
        offset=2,
    )
    cfg = BasicBlock(statements=[IRSet(place, IRGet(BlockPlace(-1, 0)))])
    assert_roundtrip(cfg)


def test_roundtrip_multiway_switch_with_mixed_conds():
    entry = BasicBlock(test=IRGet(BlockPlace(TempBlock("scrutinee", 1), 0)))
    exit_block = BasicBlock()
    targets = [BasicBlock(statements=[IRSet(BlockPlace(10000, 0), IRConst(i))]) for i in range(5)]
    for target in targets:
        target.connect_to(exit_block)
    entry.connect_to(targets[0], 3)
    entry.connect_to(targets[1], 2.5)
    entry.connect_to(targets[2], -1)
    entry.connect_to(targets[3], math.inf)
    entry.connect_to(targets[4], None)
    data = assert_roundtrip(entry)
    text = sonolus_backend.decode_cfg_debug_dump(data)
    assert "goto when" in text
    assert "default ->" in text


def test_roundtrip_self_loop():
    entry = BasicBlock(test=IRGet(BlockPlace(TempBlock("n", 1), 0)))
    exit_block = BasicBlock()
    entry.connect_to(entry, None)
    entry.connect_to(exit_block, 0)
    assert_roundtrip(entry)


def test_roundtrip_special_float_consts():
    nan_with_payload = struct.unpack("<d", struct.pack("<Q", 0x7FF8_0000_0000_BEEF))[0]
    consts = [math.nan, nan_with_payload, math.inf, -math.inf, 5.5, -0.25, 5]
    target = BlockPlace(TempBlock("v0", 1), 0)
    cfg = BasicBlock(statements=[IRSet(target, IRConst(c)) for c in consts])
    data = assert_roundtrip(cfg)
    dump = sonolus_backend.decode_cfg_canonical_dump(data)
    assert "(const f:0x7ff800000000beef)" in dump  # NaN payload preserved bit-exactly
    assert "(const f:0x7ff0000000000000)" in dump  # +inf
    assert "(const f:0xfff0000000000000)" in dump  # -inf
    assert "(const i:5)" in dump  # int tag preserved


def test_roundtrip_large_temp_block_and_odd_names():
    big = TempBlock("big", 100_000)
    weird = TempBlock('na\\me "x" \n π', 3)
    cfg = BasicBlock(
        statements=[
            IRSet(BlockPlace(big, 99_999), IRConst(1)),
            IRSet(BlockPlace(weird, 2), IRConst(2)),
            IRSet(BlockPlace(TempBlock("empty", 0), 0), IRConst(3)),
        ]
    )
    assert_roundtrip(cfg)


def test_roundtrip_int_vs_float_conds_disambiguated():
    entry = BasicBlock(test=IRGet(BlockPlace(TempBlock("x", 1), 0)))
    a = BasicBlock()
    b = BasicBlock()
    entry.connect_to(a, 2)
    entry.connect_to(b, 2.5)
    entry.connect_to(a, None)
    data = assert_roundtrip(entry)
    dump = sonolus_backend.decode_cfg_canonical_dump(data)
    assert "edge i:2 -> " in dump
    assert "edge f:0x4004000000000000 -> " in dump


def test_roundtrip_nary_instructions():
    args = [IRConst(i) for i in range(7)]
    cfg = BasicBlock(
        statements=[
            IRInstr(Op.Execute, [IRPureInstr(Op.Add, args), IRPureInstr(Op.Multiply, [])]),
        ]
    )
    assert_roundtrip(cfg)


# --- Negative tests: corrupted encodings are rejected, never crash ------------------


def small_encoded() -> bytes:
    return encode_cfg(trace_cfg(int_match_callback))


def test_truncated_data_is_rejected():
    data = encode_cfg(BasicBlock(statements=[IRSet(BlockPlace(TempBlock("v0", 1), 0), IRConst(5))]))
    for length in range(len(data)):
        with pytest.raises(ValueError, match=r"."):
            sonolus_backend.decode_cfg_canonical_dump(data[:length])


def test_bad_magic_is_rejected():
    data = bytearray(small_encoded())
    data[0] = ord("X")
    with pytest.raises(ValueError, match="magic"):
        sonolus_backend.decode_cfg_canonical_dump(bytes(data))


def test_bad_version_is_rejected():
    data = bytearray(small_encoded())
    data[4] = 99
    with pytest.raises(ValueError, match="version"):
        sonolus_backend.decode_cfg_canonical_dump(bytes(data))


def test_op_count_mismatch_is_rejected():
    data = bytearray(small_encoded())
    data[6] ^= 0xFF
    with pytest.raises(ValueError, match="op count"):
        sonolus_backend.decode_cfg_canonical_dump(bytes(data))


def test_trailing_bytes_are_rejected():
    data = small_encoded() + b"\x00"
    with pytest.raises(ValueError, match="trailing"):
        sonolus_backend.decode_cfg_canonical_dump(data)


def test_single_byte_corruption_never_crashes():
    data = small_encoded()
    for i in range(len(data)):
        corrupted = bytearray(data)
        corrupted[i] ^= 0xFF
        try:
            result = sonolus_backend.decode_cfg_canonical_dump(bytes(corrupted))
        except ValueError:
            continue  # Rejected cleanly: fine.
        # Some corruptions still decode (e.g. flipped const bits); they must
        # produce a string, not crash.
        assert isinstance(result, str)
