"""Pure-Python tests for the CFG binary encoder (no compiled extension required).

Round-trip tests against the Rust decoder live in ``test_cfg_roundtrip.py``.
"""

import math
import struct

import pytest

from sonolus.backend.encode import (
    ENCODING_VERSION,
    MAGIC,
    OP_COUNT,
    CfgEncodeError,
    cfg_canonical_dump,
    encode_cfg,
)
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock


def tiny_cfg() -> BasicBlock:
    """``{ v0 <- 5; test 0; goto exit }`` — mirrored by a Rust unit test."""
    return BasicBlock(statements=[IRSet(BlockPlace(TempBlock("v0", 1), 0), IRConst(5))])


def test_header_layout():
    data = encode_cfg(tiny_cfg())
    assert data[:4] == MAGIC
    assert int.from_bytes(data[4:6], "little") == ENCODING_VERSION
    assert int.from_bytes(data[6:8], "little") == OP_COUNT
    assert OP_COUNT == 191


def test_encoding_is_deterministic_for_same_object():
    cfg = tiny_cfg()
    assert encode_cfg(cfg) == encode_cfg(cfg)
    assert cfg_canonical_dump(cfg) == cfg_canonical_dump(cfg)


def test_encoding_is_deterministic_across_equal_builds():
    assert encode_cfg(tiny_cfg()) == encode_cfg(tiny_cfg())


def test_canonical_dump_exact_format():
    # This exact string is also asserted (against the decoded bytes) by the Rust
    # unit test `decodes_tiny_cfg`, pinning the canonical format on both sides.
    assert cfg_canonical_dump(tiny_cfg()) == (
        "cfg-canonical v1\n"
        "ops 191\n"
        "strings 1\n"
        '  string 0 "v0"\n'
        "temps 1\n"
        "  temp 0 name=0 size=1\n"
        "blocks 1\n"
        "block 0\n"
        "  stmts 1\n"
        "    (set (place b=t:0 i=i:0 o=0) (const i:5))\n"
        "  test (const i:0)\n"
        "  edges 0\n"
    )


def test_int_and_float_const_tags_are_distinct():
    block_int = BasicBlock(statements=[IRSet(BlockPlace(TempBlock("v0", 1), 0), IRConst(5))])
    block_float = BasicBlock(statements=[IRSet(BlockPlace(TempBlock("v0", 1), 0), IRConst(5.5))])
    assert "(const i:5)" in cfg_canonical_dump(block_int)
    assert f"(const f:0x{struct.unpack('<Q', struct.pack('<d', 5.5))[0]:016x})" in cfg_canonical_dump(block_float)
    assert encode_cfg(block_int) != encode_cfg(block_float)


def test_float_conds_keep_their_tag():
    entry = BasicBlock(test=IRGet(BlockPlace(TempBlock("v0", 1), 0)))
    a = BasicBlock()
    b = BasicBlock()
    entry.connect_to(a, 2)
    entry.connect_to(b, 2.5)
    entry.connect_to(b, None)
    dump = cfg_canonical_dump(entry)
    assert "edge i:2 -> " in dump
    assert f"edge f:0x{struct.unpack('<Q', struct.pack('<d', 2.5))[0]:016x} -> " in dump
    assert "edge none -> " in dump


def test_rejects_phis():
    cfg = tiny_cfg()
    cfg.phis = {SSAPlace("x", 1): {}}
    with pytest.raises(CfgEncodeError, match="phi"):
        encode_cfg(cfg)
    with pytest.raises(CfgEncodeError, match="phi"):
        cfg_canonical_dump(cfg)


def test_rejects_ssa_place():
    cfg = BasicBlock(statements=[IRSet(SSAPlace("x", 1), IRConst(0))])
    with pytest.raises(CfgEncodeError, match="SSAPlace"):
        encode_cfg(cfg)
    with pytest.raises(CfgEncodeError, match="SSAPlace"):
        cfg_canonical_dump(cfg)


def test_rejects_expr_valued_place_fields():
    expr_index = BasicBlock(statements=[IRGet(BlockPlace(10000, IRConst(1)))])
    with pytest.raises(CfgEncodeError, match=r"BlockPlace\.index"):
        encode_cfg(expr_index)
    expr_block = BasicBlock(statements=[IRGet(BlockPlace(IRPureInstr(Op.Add, []), 0))])
    with pytest.raises(CfgEncodeError, match=r"BlockPlace\.block"):
        encode_cfg(expr_block)


def test_rejects_out_of_i64_int_const():
    cfg = BasicBlock(statements=[IRSet(BlockPlace(TempBlock("v0", 1), 0), IRConst(2**70))])
    with pytest.raises(CfgEncodeError, match="i64"):
        encode_cfg(cfg)
    with pytest.raises(CfgEncodeError, match="i64"):
        cfg_canonical_dump(cfg)


def test_rejects_nan_cond():
    entry = BasicBlock()
    entry.connect_to(BasicBlock(), math.nan)
    with pytest.raises(CfgEncodeError, match="NaN"):
        encode_cfg(entry)


def test_rejects_duplicate_conds():
    entry = BasicBlock()
    a = BasicBlock()
    b = BasicBlock()
    entry.connect_to(a, 1)
    entry.connect_to(b, 1.0)  # 1 == 1.0: ordering between them would be nondeterministic
    with pytest.raises(CfgEncodeError, match=r"[Dd]uplicate"):
        encode_cfg(entry)


def test_rejects_set_outside_statement_position():
    inner_set = IRSet(BlockPlace(TempBlock("v0", 1), 0), IRConst(0))
    as_arg = BasicBlock(statements=[IRInstr(Op.Execute, [inner_set])])
    with pytest.raises(CfgEncodeError, match="IRSet"):
        encode_cfg(as_arg)
    as_test = BasicBlock(test=inner_set)
    with pytest.raises(CfgEncodeError, match="IRSet"):
        encode_cfg(as_test)


def test_rejects_unknown_statement_type():
    cfg = BasicBlock(statements=["not a statement"])
    with pytest.raises(CfgEncodeError, match="Unsupported IR node"):
        encode_cfg(cfg)
