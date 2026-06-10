"""Binary encoding of frontend-level CFGs for the Rust backend.

This module serializes the CFGs produced by tracing user callbacks (see
``sonolus.build.compile.callback_to_cfg``) into the versioned flat binary format
specified in ``rust/ENCODING.md``, decoded on the Rust side by
``sonolus-backend-core``.

Only frontend-level constructs are representable: the frontend never emits SSA, so
``SSAPlace`` and phi nodes are rejected (see the spec for the full list of rejected
constructs). All traversals here are iterative — expression trees can be deep enough
to exhaust Python's recursion limit.

``cfg_canonical_dump`` renders the canonical structural dump used for bit-exact
round-trip validation against the Rust decoder; it must stay in lockstep with the
Rust implementation in ``sonolus-backend-core/src/cfg.rs``.
"""

from __future__ import annotations

import itertools
import math
import struct
from collections.abc import Iterator

from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock, FlowEdge
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock

MAGIC = b"SCFG"
ENCODING_VERSION = 1

_OP_IDS: dict[Op, int] = {op: i for i, op in enumerate(Op)}
OP_COUNT = len(_OP_IDS)

_I64_MIN = -(2**63)
_I64_MAX = 2**63 - 1

# Node tags
_TAG_CONST_INT = 0
_TAG_CONST_FLOAT = 1
_TAG_PURE_INSTR = 2
_TAG_INSTR = 3
_TAG_GET = 4
_TAG_SET = 5

# BlockPlace.block tags
_BLOCK_INT = 0
_BLOCK_TEMP = 1
_BLOCK_PLACE = 2

# BlockPlace.index tags
_INDEX_INT = 0
_INDEX_PLACE = 1

# Edge cond tags
_COND_NONE = 0
_COND_INT = 1
_COND_FLOAT = 2


class CfgEncodeError(ValueError):
    """Raised when a CFG contains constructs not representable in the encoding."""


class _Writer:
    __slots__ = ("buf",)

    def __init__(self):
        self.buf = bytearray()

    def u8(self, value: int):
        self.buf.append(value)

    def u16(self, value: int):
        self.buf += value.to_bytes(2, "little")

    def varuint(self, value: int):
        if value < 0:
            raise CfgEncodeError(f"Cannot encode negative value {value} as varuint")
        if value > 2**64 - 1:
            raise CfgEncodeError(f"Value {value} is out of range for varuint")
        while True:
            bits = value & 0x7F
            value >>= 7
            if value:
                self.buf.append(bits | 0x80)
            else:
                self.buf.append(bits)
                return

    def varint(self, value: int):
        if not _I64_MIN <= value <= _I64_MAX:
            raise CfgEncodeError(f"Integer {value} is out of range for the encoding (must fit in i64)")
        self.varuint(((value << 1) ^ (value >> 63)) & (2**64 - 1))

    def f64(self, value: float):
        self.buf += struct.pack("<d", value)


class _Tables:
    """String and temp block tables, in first encounter order."""

    __slots__ = ("_string_ids", "_temp_ids", "strings", "temp_blocks")

    def __init__(self):
        self.strings: list[str] = []
        self._string_ids: dict[str, int] = {}
        self.temp_blocks: list[TempBlock] = []
        self._temp_ids: dict[tuple[str, int], int] = {}

    def string_id(self, value: str) -> int:
        existing = self._string_ids.get(value)
        if existing is not None:
            return existing
        index = len(self.strings)
        self.strings.append(value)
        self._string_ids[value] = index
        return index

    def temp_id(self, temp_block: TempBlock) -> int:
        if not isinstance(temp_block.name, str):
            raise CfgEncodeError(f"TempBlock name must be a str, got {type(temp_block.name).__name__}")
        if not isinstance(temp_block.size, int) or isinstance(temp_block.size, bool) or temp_block.size < 0:
            raise CfgEncodeError(f"TempBlock size must be a non-negative int, got {temp_block.size!r}")
        key = (temp_block.name, temp_block.size)
        existing = self._temp_ids.get(key)
        if existing is not None:
            return existing
        self.string_id(temp_block.name)
        index = len(self.temp_blocks)
        self.temp_blocks.append(temp_block)
        self._temp_ids[key] = index
        return index


def _reverse_postorder(entry: BasicBlock) -> list[BasicBlock]:
    """Iterative equivalent of ``flow.traverse_cfg_reverse_postorder`` (same ordering)."""

    def sorted_edges(block: BasicBlock) -> list[FlowEdge]:
        return sorted(block.outgoing, key=lambda e: (e.cond is None, e.cond))

    visited = {entry}
    postorder = []
    stack: list[tuple[BasicBlock, Iterator[FlowEdge]]] = [(entry, iter(sorted_edges(entry)))]
    while stack:
        block, edge_iter = stack[-1]
        descended = False
        for edge in edge_iter:
            dst = edge.dst
            if dst in visited:
                continue
            visited.add(dst)
            stack.append((dst, iter(sorted_edges(dst))))
            descended = True
            break
        if not descended:
            postorder.append(block)
            stack.pop()
    postorder.reverse()
    return postorder


def _checked_sorted_edges(block: BasicBlock) -> list[tuple[float | int | None, BasicBlock]]:
    edges: list[tuple[float | int | None, BasicBlock]] = []
    for edge in block.outgoing:
        cond = edge.cond
        if isinstance(cond, bool):
            cond = int(cond)
        elif cond is not None and not isinstance(cond, int | float):
            raise CfgEncodeError(f"Unsupported edge cond type: {type(cond).__name__}")
        if isinstance(cond, float) and math.isnan(cond):
            raise CfgEncodeError("NaN edge conds are not supported (they make edge ordering nondeterministic)")
        edges.append((cond, edge.dst))
    edges.sort(key=lambda entry: (entry[0] is None, entry[0]))
    for (cond_a, _), (cond_b, _) in itertools.pairwise(edges):
        if cond_a == cond_b or (cond_a is None and cond_b is None):
            raise CfgEncodeError(f"Duplicate edge cond {cond_a!r} on a block")
    return edges


def _write_const(writer: _Writer, value: float | int):
    if isinstance(value, bool):
        raise CfgEncodeError("bool IRConst values are not expected at the frontend level")
    if isinstance(value, int):
        writer.u8(_TAG_CONST_INT)
        writer.varint(value)
    elif isinstance(value, float):
        writer.u8(_TAG_CONST_FLOAT)
        writer.f64(value)
    else:
        raise CfgEncodeError(f"Unsupported IRConst value type: {type(value).__name__}")


# States for the iterative place writer
_P_PLACE = 0
_P_INDEX = 1
_P_OFFSET = 2


def _write_place(writer: _Writer, place: object, tables: _Tables):
    stack: list[tuple[int, object]] = [(_P_PLACE, place)]
    while stack:
        state, current = stack.pop()
        if state == _P_PLACE:
            if isinstance(current, SSAPlace):
                raise CfgEncodeError("SSAPlace is not representable in the frontend-level CFG encoding")
            if not isinstance(current, BlockPlace):
                raise CfgEncodeError(f"Unsupported place type: {type(current).__name__}")
            stack.append((_P_INDEX, current))
            block = current.block
            if isinstance(block, TempBlock):
                writer.u8(_BLOCK_TEMP)
                writer.varuint(tables.temp_id(block))
            elif isinstance(block, BlockPlace):
                writer.u8(_BLOCK_PLACE)
                stack.append((_P_PLACE, block))
            elif isinstance(block, SSAPlace):
                raise CfgEncodeError("SSAPlace is not representable in the frontend-level CFG encoding")
            elif isinstance(block, int):  # Includes BlockData enum members (int subclasses) and bool
                writer.u8(_BLOCK_INT)
                writer.varint(int(block))
            else:
                raise CfgEncodeError(f"Unsupported BlockPlace.block type: {type(block).__name__}")
        elif state == _P_INDEX:
            stack.append((_P_OFFSET, current))
            index = current.index
            if isinstance(index, BlockPlace):
                writer.u8(_INDEX_PLACE)
                stack.append((_P_PLACE, index))
            elif isinstance(index, SSAPlace):
                raise CfgEncodeError("SSAPlace is not representable in the frontend-level CFG encoding")
            elif isinstance(index, int):  # Includes bool, normalized to int
                writer.u8(_INDEX_INT)
                writer.varint(int(index))
            else:
                raise CfgEncodeError(f"Unsupported BlockPlace.index type: {type(index).__name__}")
        else:
            offset = current.offset
            if not isinstance(offset, int) or isinstance(offset, bool):
                raise CfgEncodeError(f"Unsupported BlockPlace.offset type: {type(offset).__name__}")
            writer.varint(offset)


def _write_node(writer: _Writer, node: object, tables: _Tables, *, allow_set: bool):
    stack: list[tuple[object, bool]] = [(node, allow_set)]
    while stack:
        current, set_allowed = stack.pop()
        if isinstance(current, IRConst):
            _write_const(writer, current.value)
        elif isinstance(current, IRPureInstr):
            writer.u8(_TAG_PURE_INSTR)
            writer.u16(_OP_IDS[current.op])
            writer.varuint(len(current.args))
            stack.extend((arg, False) for arg in reversed(current.args))
        elif isinstance(current, IRInstr):
            writer.u8(_TAG_INSTR)
            writer.u16(_OP_IDS[current.op])
            writer.varuint(len(current.args))
            stack.extend((arg, False) for arg in reversed(current.args))
        elif isinstance(current, IRGet):
            writer.u8(_TAG_GET)
            _write_place(writer, current.place, tables)
        elif isinstance(current, IRSet):
            if not set_allowed:
                raise CfgEncodeError("IRSet is only allowed as a top-level statement")
            writer.u8(_TAG_SET)
            _write_place(writer, current.place, tables)
            stack.append((current.value, False))
        else:
            raise CfgEncodeError(f"Unsupported IR node type: {type(current).__name__}")


def encode_cfg(entry: BasicBlock) -> bytes:
    """Encodes a frontend-level CFG into the versioned flat binary format.

    Args:
        entry: The entry basic block of the CFG.

    Returns:
        The encoded bytes (see ``rust/ENCODING.md``).

    Raises:
        CfgEncodeError: If the CFG contains constructs not representable in the
            encoding (SSA places, phi nodes, expression-valued place fields, ...).
    """
    blocks = _reverse_postorder(entry)
    block_ids = {block: i for i, block in enumerate(blocks)}
    tables = _Tables()
    body = _Writer()
    body.varuint(len(blocks))
    for block in blocks:
        if block.phis:
            raise CfgEncodeError("CFGs with phi nodes are not representable (frontend CFGs are not in SSA form)")
        body.varuint(len(block.statements))
        for stmt in block.statements:
            _write_node(body, stmt, tables, allow_set=True)
        _write_node(body, block.test, tables, allow_set=False)
        edges = _checked_sorted_edges(block)
        body.varuint(len(edges))
        for cond, dst in edges:
            if cond is None:
                body.u8(_COND_NONE)
            elif isinstance(cond, int):
                body.u8(_COND_INT)
                body.varint(cond)
            else:
                body.u8(_COND_FLOAT)
                body.f64(cond)
            body.varuint(block_ids[dst])

    out = _Writer()
    out.buf += MAGIC
    out.u16(ENCODING_VERSION)
    out.u16(OP_COUNT)
    out.varuint(len(tables.strings))
    for string in tables.strings:
        data = string.encode("utf-8")
        out.varuint(len(data))
        out.buf += data
    out.varuint(len(tables.temp_blocks))
    for temp_block in tables.temp_blocks:
        out.varuint(tables.string_id(temp_block.name))
        out.varuint(temp_block.size)
    return bytes(out.buf + body.buf)


def _float_bits(value: float) -> str:
    return f"0x{struct.unpack('<Q', struct.pack('<d', value))[0]:016x}"


def _escape(value: str) -> str:
    parts = []
    for byte in value.encode("utf-8"):
        if byte == 0x22:
            parts.append('\\"')
        elif byte == 0x5C:
            parts.append("\\\\")
        elif 0x20 <= byte <= 0x7E:
            parts.append(chr(byte))
        else:
            parts.append(f"\\x{byte:02x}")
    return "".join(parts)


def _const_canonical(value: float | int) -> str:
    if isinstance(value, bool):
        raise CfgEncodeError("bool IRConst values are not expected at the frontend level")
    if isinstance(value, int):
        if not _I64_MIN <= value <= _I64_MAX:
            raise CfgEncodeError(f"Integer {value} is out of range for the encoding (must fit in i64)")
        return f"(const i:{value})"
    if isinstance(value, float):
        return f"(const f:{_float_bits(value)})"
    raise CfgEncodeError(f"Unsupported IRConst value type: {type(value).__name__}")


# Token kinds for the iterative canonical renderer (literal strings are pushed directly)
_T_NODE = 0
_T_NODE_NO_SET = 1
_T_PLACE = 2


def _node_canonical(node: object, tables: _Tables, *, allow_set: bool) -> str:
    out: list[str] = []
    stack: list[object] = [(_T_NODE if allow_set else _T_NODE_NO_SET, node)]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
            continue
        kind, current = item
        if kind == _T_PLACE:
            if isinstance(current, SSAPlace):
                raise CfgEncodeError("SSAPlace is not representable in the frontend-level CFG encoding")
            if not isinstance(current, BlockPlace):
                raise CfgEncodeError(f"Unsupported place type: {type(current).__name__}")
            offset = current.offset
            if not isinstance(offset, int) or isinstance(offset, bool):
                raise CfgEncodeError(f"Unsupported BlockPlace.offset type: {type(offset).__name__}")
            if not _I64_MIN <= offset <= _I64_MAX:
                raise CfgEncodeError(f"Integer {offset} is out of range for the encoding (must fit in i64)")
            out.append("(place b=")
            stack.append(f" o={offset})")
            index = current.index
            if isinstance(index, BlockPlace):
                stack.append((_T_PLACE, index))
            elif isinstance(index, SSAPlace):
                raise CfgEncodeError("SSAPlace is not representable in the frontend-level CFG encoding")
            elif isinstance(index, int):  # Includes bool, normalized to int
                value = int(index)
                if not _I64_MIN <= value <= _I64_MAX:
                    raise CfgEncodeError(f"Integer {value} is out of range for the encoding (must fit in i64)")
                stack.append(f"i:{value}")
            else:
                raise CfgEncodeError(f"Unsupported BlockPlace.index type: {type(index).__name__}")
            stack.append(" i=")
            block = current.block
            if isinstance(block, TempBlock):
                stack.append(f"t:{tables.temp_id(block)}")
            elif isinstance(block, BlockPlace):
                stack.append((_T_PLACE, block))
            elif isinstance(block, SSAPlace):
                raise CfgEncodeError("SSAPlace is not representable in the frontend-level CFG encoding")
            elif isinstance(block, int):  # Includes BlockData enum members and bool
                value = int(block)
                if not _I64_MIN <= value <= _I64_MAX:
                    raise CfgEncodeError(f"Integer {value} is out of range for the encoding (must fit in i64)")
                stack.append(f"i:{value}")
            else:
                raise CfgEncodeError(f"Unsupported BlockPlace.block type: {type(block).__name__}")
        elif isinstance(current, IRConst):
            out.append(_const_canonical(current.value))
        elif isinstance(current, IRPureInstr | IRInstr):
            label = "pure" if isinstance(current, IRPureInstr) else "instr"
            out.append(f"({label} {_OP_IDS[current.op]}")
            stack.append(")")
            for arg in reversed(current.args):
                stack.append((_T_NODE_NO_SET, arg))
                stack.append(" ")
        elif isinstance(current, IRGet):
            out.append("(get ")
            stack.append(")")
            stack.append((_T_PLACE, current.place))
        elif isinstance(current, IRSet):
            if kind != _T_NODE:
                raise CfgEncodeError("IRSet is only allowed as a top-level statement")
            out.append("(set ")
            stack.append(")")
            stack.append((_T_NODE_NO_SET, current.value))
            stack.append(" ")
            stack.append((_T_PLACE, current.place))
        else:
            raise CfgEncodeError(f"Unsupported IR node type: {type(current).__name__}")
    return "".join(out)


def _cond_canonical(cond: float | int | None) -> str:
    if cond is None:
        return "none"
    if isinstance(cond, int):
        if not _I64_MIN <= cond <= _I64_MAX:
            raise CfgEncodeError(f"Integer {cond} is out of range for the encoding (must fit in i64)")
        return f"i:{cond}"
    return f"f:{_float_bits(cond)}"


def cfg_canonical_dump(entry: BasicBlock) -> str:
    """Renders the canonical structural dump of a frontend-level CFG.

    The output is byte-identical to the Rust side's canonical dump of the decoded
    encoding (``sonolus_backend.decode_cfg_canonical_dump(encode_cfg(entry))``); see
    ``rust/ENCODING.md`` §5. Floats are rendered as raw IEEE-754 bits so the
    comparison is bit-exact, including NaN payloads.

    Args:
        entry: The entry basic block of the CFG.

    Returns:
        The canonical dump as a string.

    Raises:
        CfgEncodeError: If the CFG contains constructs not representable in the
            encoding.
    """
    blocks = _reverse_postorder(entry)
    block_ids = {block: i for i, block in enumerate(blocks)}
    tables = _Tables()
    block_lines: list[str] = []
    for block_id, block in enumerate(blocks):
        if block.phis:
            raise CfgEncodeError("CFGs with phi nodes are not representable (frontend CFGs are not in SSA form)")
        block_lines.append(f"block {block_id}")
        block_lines.append(f"  stmts {len(block.statements)}")
        block_lines.extend(f"    {_node_canonical(stmt, tables, allow_set=True)}" for stmt in block.statements)
        block_lines.append(f"  test {_node_canonical(block.test, tables, allow_set=False)}")
        edges = _checked_sorted_edges(block)
        block_lines.append(f"  edges {len(edges)}")
        block_lines.extend(f"    edge {_cond_canonical(cond)} -> {block_ids[dst]}" for cond, dst in edges)

    lines = [f"cfg-canonical v{ENCODING_VERSION}", f"ops {OP_COUNT}", f"strings {len(tables.strings)}"]
    lines.extend(f'  string {i} "{_escape(string)}"' for i, string in enumerate(tables.strings))
    lines.append(f"temps {len(tables.temp_blocks)}")
    lines.extend(
        f"  temp {i} name={tables.string_id(temp_block.name)} size={temp_block.size}"
        for i, temp_block in enumerate(tables.temp_blocks)
    )
    lines.append(f"blocks {len(blocks)}")
    lines.extend(block_lines)
    return "\n".join(lines) + "\n"
