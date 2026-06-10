# CFG Binary Encoding (version 1)

Specification of the versioned flat binary format used to move **frontend-level** CFGs
from Python (`sonolus/backend/encode.py`) to Rust
(`sonolus-backend-core::decode::decode_cfg`) across the FFI boundary.

The format only represents what the Python frontend actually emits. The frontend never
produces SSA, so `SSAPlace` and phi nodes are **not representable**: the encoder raises
on any CFG containing them, and there are no tags reserved for them. The same applies to
the other rejected constructs listed in [§7](#7-constructs-rejected-by-the-encoder).

Producer and consumer are released in lockstep (the encoder ships in the same wheel as
the compiled decoder), so version negotiation is unnecessary: the decoder rejects any
version other than the one it was built for.

## 1. Primitives

All multi-byte fixed-width values are **little-endian**.

| Name      | Encoding |
|-----------|----------|
| `u8`      | 1 byte |
| `u16`     | 2 bytes LE |
| `f64`     | 8 bytes LE, raw IEEE-754 bits (NaN payloads and signs are preserved bit-exactly) |
| `varuint` | LEB128: 7 bits per byte, low bits first, high bit = continuation. Max 10 bytes; must fit in `u64`. The encoder always emits minimal form (part of the determinism contract); the decoder validates only the `u64` range. |
| `varint`  | ZigZag-mapped `i64` stored as `varuint`: `zigzag(v) = (v << 1) ^ (v >> 63)` |
| `string`  | `varuint` byte length, then that many bytes of UTF-8 (decoder validates UTF-8) |

## 2. Top-level layout

```
header
string table
temp block table
blocks
```

The decoder errors on trailing bytes after the last block.

### 2.1 Header (8 bytes)

| Field      | Type | Value |
|------------|------|-------|
| magic      | 4 bytes | `b"SCFG"` |
| version    | `u16` | `1` |
| `op_count` | `u16` | number of ops in the producer's op table (`191` for this version) |

`op_count` must equal the decoder's `Op::COUNT` exactly. Op ids are the 0-based
definition-order indices of `sonolus/backend/ops.py` (the T0.2 contract: append-only;
any reorder/removal — and, conservatively, any append — is an encoding-relevant change
caught by this field). A mismatch is a decode error.

### 2.2 String table

`varuint` count, then that many `string`s. Strings are deduplicated and appear in first
encounter order during block serialization (the only strings are `TempBlock` names).

### 2.3 Temp block table

`varuint` count, then per entry:

| Field  | Type | Meaning |
|--------|------|---------|
| name   | `varuint` | index into the string table |
| size   | `varuint` | `TempBlock.size` (size 0 is legal) |

Entries are deduplicated by `(name, size)` and appear in first encounter order. Two
entries may share a name with different sizes (Python `TempBlock` equality is by
`(name, size)`).

### 2.4 Blocks

`varuint` block count, then that many block records. Blocks are numbered `0..count` in
**reverse postorder** from the entry block (matching
`sonolus.backend.optimize.flow.traverse_cfg_reverse_postorder`, whose DFS visits
outgoing edges sorted by `(cond is None, cond)`). Block 0 is the entry block. Only
blocks reachable from the entry are encoded; `incoming` edge sets are reconstructible
from `outgoing` and are not encoded.

Each block record:

```
varuint statement_count
statement_count × node        (each statement is one prefix-encoded node tree)
node                          (the test expression; Set is not allowed)
varuint edge_count
edge_count × edge
```

Each edge (edges are sorted by `(cond is None, cond)`, the same key the Python dump and
traversal code uses; the encoder rejects duplicate conds, so this order is total):

```
u8 cond_tag      0 = none (unconditional/default), 1 = int, 2 = float
payload          tag 0: none; tag 1: varint; tag 2: f64
varuint target   block number; must be < block count
```

The int-vs-float distinction of conds is load-bearing and preserved exactly. Python
`bool` conds are normalized to int (`True` → `1`) by the encoder.

## 3. Node encoding

Nodes are prefix-encoded trees: a `u8` tag, fixed fields, then child nodes. Ops are
serialized as `u16` ids (see §2.1).

| Tag | Node | Layout after tag |
|-----|------|------------------|
| 0 | `IRConst` (int)   | `varint` value |
| 1 | `IRConst` (float) | `f64` value |
| 2 | `IRPureInstr`     | `u16` op id, `varuint` argc, argc × node |
| 3 | `IRInstr`         | `u16` op id, `varuint` argc, argc × node |
| 4 | `IRGet`           | place |
| 5 | `IRSet`           | place, then one node (the value) |

Rules enforced by both encoder and decoder:

- Tag 2 requires `op.pure` (mirrors the `IRPureInstr` constructor assert).
- `IRSet` (tag 5) is only legal as a top-level statement. It may not appear as a test,
  an instruction argument, or the value of another `IRSet`.
- Instructions are **n-ary** at this level; args are encoded as-is. (The binary-IR
  invariant applies to the later mid-level IR, not to this encoding.)
- Int consts are tagged int and float consts tagged float exactly as in Python
  (`IRConst.__new__` already canonicalizes integral floats — including `-0.0` and
  `True` — to int, so a float const here is never integral-valued in practice, but the
  decoder does not enforce that).

## 4. Place encoding

Only `BlockPlace` is representable. Layout:

```
u8 block_tag     0 = int, 1 = temp block, 2 = nested place
payload          tag 0: varint; tag 1: varuint temp-block-table index; tag 2: place
u8 index_tag     0 = int, 1 = nested place
payload          tag 0: varint; tag 1: place
varint offset
```

### Which `BlockPlace` variants exist at the frontend level

The Python type annotations allow
`block: Block | int | TempBlock | Place | IRExpr` and `index: int | Place | IRExpr`,
but the frontend only ever constructs:

- **block**: a `BlockEnum` member (an `int` subclass — encoded as its plain int id),
  a raw `int` (test harnesses use negative ids), a `TempBlock`, or a nested
  `BlockPlace` (from `_deref` with a runtime block id: `Num.index()` returns
  `int | BlockPlace`).
- **index**: an `int`, or a nested `BlockPlace` (dynamic indexing; again via
  `Num.index()`).

`IRExpr` as block/index and `SSAPlace` anywhere occur only inside the optimizer (which
this format predates by construction) and are **rejected** by the encoder; the decoder
has no tags for them.

## 5. Canonical structural dump

Round-trip validation compares a canonical, structural, bit-exact text dump produced
independently by both sides (Python: `sonolus.backend.encode.cfg_canonical_dump` from
the live CFG objects; Rust: `sonolus_backend_core::cfg::canonical_dump` from the
decoded arena). The dumps must be byte-identical. This is deliberately *not* the debug
dump and not Python-`repr`-compatible (decision D7).

- **All floats are rendered as their raw IEEE-754 bits** (`f:0x%016x`), so NaN payloads,
  `-0.0`, and infinities compare exactly. (Hex-float was rejected because it cannot
  distinguish NaN payloads.)
- Ints are rendered as decimal `i64` with the explicit `i:` tag.
- Strings are rendered byte-wise from UTF-8 with `\"`, `\\`, and `\xNN` escapes for
  bytes outside printable ASCII.
- Newlines are `\n`.

Grammar (indentation as shown, two spaces per level):

```
cfg-canonical v<version>
ops <op_count>
strings <n>
  string <i> "<escaped>"
temps <n>
  temp <i> name=<string index> size=<size>
blocks <n>
block <i>
  stmts <n>
    <node>
  test <node>
  edges <n>
    edge <cond> -> <target>
```

with

```
<node>  := (const i:<dec>) | (const f:0x<16 hex digits>)
         | (pure <op id> {SP <node>}*) | (instr <op id> {SP <node>}*)
         | (get <place>) | (set <place> <node>)
<place> := (place b=<bv> i=<iv> o=<dec>)
<bv>    := i:<dec> | t:<temp index> | <place>
<iv>    := i:<dec> | <place>
<cond>  := none | i:<dec> | f:0x<16 hex digits>
```

## 6. Debug dump

`sonolus_backend_core::cfg::cfg_to_text` is a human-readable dump in the spirit of the
Python `cfg_to_text` (block labels, `goto` forms incl. `goto X if t else Y` and
`goto when t`), but uses Rust's native float formatting and uniform `OpName(args)`
expression rendering (decision D7: no Python-repr compatibility). It carries no
compatibility guarantees and is exposed to Python as
`sonolus_backend.decode_cfg_debug_dump` for inspection only.

## 7. Constructs rejected by the encoder

The encoder raises `CfgEncodeError` (a `ValueError`) on:

- non-empty `phis` on any block (SSA constructs; never emitted by the frontend),
- `SSAPlace` anywhere (as a place, block, or index),
- `IRExpr` (or anything other than the §4 variants) as `BlockPlace.block`/`index`,
- non-`int` `BlockPlace.offset`,
- int constants outside `i64` (Python ints are unbounded; the runtime is f64-only, so
  anything beyond ±2^63 is far past exact representability — encountered in practice it
  would mean a bug upstream; a future version can add a bignum tag if ever needed),
- NaN edge conds (unsortable, would make block numbering nondeterministic),
- duplicate edge conds on one block under numeric equality, e.g. both `0` and `0.0`
  (impossible at the frontend, where conds are dict keys; equal conds would make the
  sorted edge order nondeterministic),
- `IRSet` as a test/argument/Set-value, and unknown statement/test object types.

## 8. Determinism

Encoding the same CFG always yields the same bytes: block order is reverse postorder
with a total edge sort (guaranteed by the NaN/duplicate-cond rejections), tables are
first-encounter-ordered, statements/args/places are serialized in their inherent order,
and varuints are minimal-form. Both canonical dumps are pure functions of that same
structure.

## 9. Implementation constraints

- No recursion over user-sized structures on either side (PORT.md invariant §3.4):
  encoder, decoder, and both dump implementations use explicit work stacks (expression
  trees and nested places can be deep).
- The decoder validates everything (magic, version, op count, op ids, purity, tags,
  table/block indices, UTF-8, truncation, trailing bytes) and returns `Result`; corrupt
  input must produce an error, never a panic. All counts are sanity-capped by the
  remaining input length before any allocation.
- No `unsafe` in `sonolus-backend-core`.

## 10. Version history

- **v1** — initial format (this document).
