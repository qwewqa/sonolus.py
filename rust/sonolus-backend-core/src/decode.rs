//! Decoder for the frontend-level CFG binary encoding (see `rust/ENCODING.md`).
//!
//! The decoder validates everything and returns [`Result`]; corrupt input must
//! produce a [`DecodeError`], never a panic. All counts are sanity-capped by the
//! remaining input length before any allocation, and all traversals are iterative.

use std::fmt;

use crate::cfg::{
    BasicBlock, BlockValue, Cfg, Edge, EdgeCond, IndexValue, Node, NodeId, Place, PlaceId,
};
use crate::ops::Op;

/// The 4-byte magic at the start of every encoded CFG.
pub const MAGIC: &[u8; 4] = b"SCFG";
/// The format version this decoder supports.
pub const ENCODING_VERSION: u16 = 1;

// Node tags
const TAG_CONST_INT: u8 = 0;
const TAG_CONST_FLOAT: u8 = 1;
const TAG_PURE_INSTR: u8 = 2;
const TAG_INSTR: u8 = 3;
const TAG_GET: u8 = 4;
const TAG_SET: u8 = 5;

// BlockPlace.block tags
const BLOCK_INT: u8 = 0;
const BLOCK_TEMP: u8 = 1;
const BLOCK_PLACE: u8 = 2;

// BlockPlace.index tags
const INDEX_INT: u8 = 0;
const INDEX_PLACE: u8 = 1;

// Edge cond tags
const COND_NONE: u8 = 0;
const COND_INT: u8 = 1;
const COND_FLOAT: u8 = 2;

/// An error produced while decoding an encoded CFG.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DecodeError {
    /// Input ended before the structure was complete.
    UnexpectedEof,
    /// The input does not start with [`MAGIC`].
    BadMagic,
    /// The header version is not [`ENCODING_VERSION`].
    UnsupportedVersion(u16),
    /// The header op count does not match this build's [`Op::COUNT`].
    OpCountMismatch { expected: u16, found: u16 },
    /// A varuint exceeded 64 bits.
    VaruintOverflow,
    /// A string table entry is not valid UTF-8.
    InvalidUtf8,
    /// A count field exceeds what the remaining input could possibly hold.
    CountTooLarge(&'static str),
    /// A temp block entry references a string index outside the string table.
    BadStringIndex(u64),
    /// A place references a temp block index outside the temp block table.
    BadTempBlockIndex(u64),
    /// An instruction references an unknown op id.
    BadOpId(u16),
    /// A pure-instruction tag carries an op that is not pure.
    OpNotPure(u16),
    /// An unknown node tag.
    BadNodeTag(u8),
    /// An unknown `BlockPlace.block` tag.
    BadBlockValueTag(u8),
    /// An unknown `BlockPlace.index` tag.
    BadIndexValueTag(u8),
    /// An unknown edge cond tag.
    BadCondTag(u8),
    /// An edge targets a block index outside the block list.
    BadBlockTarget(u64),
    /// A `Set` node appeared somewhere other than as a top-level statement.
    SetNotAllowedHere,
    /// Input continues past the end of the encoded CFG.
    TrailingBytes,
}

impl fmt::Display for DecodeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnexpectedEof => write!(f, "unexpected end of input"),
            Self::BadMagic => write!(f, "bad magic (expected b\"SCFG\")"),
            Self::UnsupportedVersion(v) => {
                write!(
                    f,
                    "unsupported encoding version {v} (expected {ENCODING_VERSION})"
                )
            }
            Self::OpCountMismatch { expected, found } => {
                write!(
                    f,
                    "op count mismatch: encoded with {found} ops, decoder built for {expected}"
                )
            }
            Self::VaruintOverflow => write!(f, "varuint exceeds 64 bits"),
            Self::InvalidUtf8 => write!(f, "string table entry is not valid UTF-8"),
            Self::CountTooLarge(what) => write!(f, "{what} count exceeds remaining input size"),
            Self::BadStringIndex(i) => write!(f, "string index {i} out of range"),
            Self::BadTempBlockIndex(i) => write!(f, "temp block index {i} out of range"),
            Self::BadOpId(id) => write!(f, "unknown op id {id}"),
            Self::OpNotPure(id) => {
                write!(f, "op id {id} used in a pure instruction but is not pure")
            }
            Self::BadNodeTag(t) => write!(f, "unknown node tag {t}"),
            Self::BadBlockValueTag(t) => write!(f, "unknown block value tag {t}"),
            Self::BadIndexValueTag(t) => write!(f, "unknown index value tag {t}"),
            Self::BadCondTag(t) => write!(f, "unknown edge cond tag {t}"),
            Self::BadBlockTarget(i) => write!(f, "edge target block {i} out of range"),
            Self::SetNotAllowedHere => {
                write!(f, "Set node is only allowed as a top-level statement")
            }
            Self::TrailingBytes => write!(f, "trailing bytes after encoded CFG"),
        }
    }
}

impl std::error::Error for DecodeError {}

struct Reader<'a> {
    data: &'a [u8],
    pos: usize,
}

impl<'a> Reader<'a> {
    fn new(data: &'a [u8]) -> Self {
        Self { data, pos: 0 }
    }

    fn remaining(&self) -> usize {
        self.data.len() - self.pos
    }

    fn bytes(&mut self, n: usize) -> Result<&'a [u8], DecodeError> {
        let end = self.pos.checked_add(n).ok_or(DecodeError::UnexpectedEof)?;
        if end > self.data.len() {
            return Err(DecodeError::UnexpectedEof);
        }
        let out = &self.data[self.pos..end];
        self.pos = end;
        Ok(out)
    }

    fn u8(&mut self) -> Result<u8, DecodeError> {
        Ok(self.bytes(1)?[0])
    }

    fn u16(&mut self) -> Result<u16, DecodeError> {
        let b = self.bytes(2)?;
        Ok(u16::from_le_bytes([b[0], b[1]]))
    }

    fn f64(&mut self) -> Result<f64, DecodeError> {
        let b = self.bytes(8)?;
        let mut buf = [0u8; 8];
        buf.copy_from_slice(b);
        Ok(f64::from_le_bytes(buf))
    }

    fn varuint(&mut self) -> Result<u64, DecodeError> {
        let mut value: u64 = 0;
        let mut shift: u32 = 0;
        loop {
            let byte = self.u8()?;
            let low = u64::from(byte & 0x7f);
            if shift > 63 || (shift == 63 && low > 1) {
                return Err(DecodeError::VaruintOverflow);
            }
            value |= low << shift;
            if byte & 0x80 == 0 {
                return Ok(value);
            }
            shift += 7;
        }
    }

    #[allow(clippy::cast_possible_wrap)]
    fn varint(&mut self) -> Result<i64, DecodeError> {
        let v = self.varuint()?;
        Ok(((v >> 1) as i64) ^ -((v & 1) as i64))
    }

    /// Reads a count and sanity-caps it by the remaining input (every counted item
    /// occupies at least one byte), so corrupt counts cannot trigger huge allocations.
    fn count(&mut self, what: &'static str) -> Result<usize, DecodeError> {
        let v = self.varuint()?;
        if v > self.remaining() as u64 {
            return Err(DecodeError::CountTooLarge(what));
        }
        usize::try_from(v).map_err(|_| DecodeError::CountTooLarge(what))
    }
}

struct PlaceFrame {
    block: Option<BlockValue>,
    index: Option<IndexValue>,
}

fn decode_place(r: &mut Reader<'_>, cfg: &mut Cfg) -> Result<PlaceId, DecodeError> {
    let mut frames = vec![PlaceFrame {
        block: None,
        index: None,
    }];
    loop {
        let top = frames
            .last_mut()
            .expect("place frame stack is never empty here");
        if top.block.is_none() {
            let tag = r.u8()?;
            match tag {
                BLOCK_INT => top.block = Some(BlockValue::Int(r.varint()?)),
                BLOCK_TEMP => {
                    let i = r.varuint()?;
                    let idx = usize::try_from(i).map_err(|_| DecodeError::BadTempBlockIndex(i))?;
                    if idx >= cfg.temp_blocks.len() {
                        return Err(DecodeError::BadTempBlockIndex(i));
                    }
                    top.block = Some(BlockValue::Temp(idx));
                }
                BLOCK_PLACE => frames.push(PlaceFrame {
                    block: None,
                    index: None,
                }),
                t => return Err(DecodeError::BadBlockValueTag(t)),
            }
        } else if top.index.is_none() {
            let tag = r.u8()?;
            match tag {
                INDEX_INT => top.index = Some(IndexValue::Int(r.varint()?)),
                INDEX_PLACE => frames.push(PlaceFrame {
                    block: None,
                    index: None,
                }),
                t => return Err(DecodeError::BadIndexValueTag(t)),
            }
        } else {
            let offset = r.varint()?;
            let frame = frames.pop().expect("just inspected the top frame");
            let id = cfg.places.len();
            cfg.places.push(Place {
                block: frame.block.expect("block was filled before completion"),
                index: frame.index.expect("index was filled before completion"),
                offset,
            });
            match frames.last_mut() {
                None => return Ok(id),
                Some(parent) => {
                    // A child place fills whichever field caused the descent: the
                    // block if it is still empty, otherwise the index.
                    if parent.block.is_none() {
                        parent.block = Some(BlockValue::Place(id));
                    } else {
                        parent.index = Some(IndexValue::Place(id));
                    }
                }
            }
        }
    }
}

enum NodeFrame {
    Args {
        pure: bool,
        op: Op,
        argc: usize,
        args: Vec<NodeId>,
    },
    SetValue {
        place: PlaceId,
    },
}

fn decode_node(r: &mut Reader<'_>, cfg: &mut Cfg, allow_set: bool) -> Result<NodeId, DecodeError> {
    fn push_node(cfg: &mut Cfg, node: Node) -> NodeId {
        let id = cfg.nodes.len();
        cfg.nodes.push(node);
        id
    }

    let mut frames: Vec<NodeFrame> = Vec::new();
    loop {
        let tag = r.u8()?;
        let completed: Option<NodeId> = match tag {
            TAG_CONST_INT => Some(push_node(cfg, Node::ConstInt(r.varint()?))),
            TAG_CONST_FLOAT => Some(push_node(cfg, Node::ConstFloat(r.f64()?))),
            TAG_PURE_INSTR | TAG_INSTR => {
                let op_id = r.u16()?;
                let op = Op::from_id(op_id).ok_or(DecodeError::BadOpId(op_id))?;
                let pure = tag == TAG_PURE_INSTR;
                if pure && !op.pure() {
                    return Err(DecodeError::OpNotPure(op_id));
                }
                let argc = r.count("instruction argument")?;
                if argc == 0 {
                    let node = if pure {
                        Node::PureInstr {
                            op,
                            args: Vec::new(),
                        }
                    } else {
                        Node::Instr {
                            op,
                            args: Vec::new(),
                        }
                    };
                    Some(push_node(cfg, node))
                } else {
                    frames.push(NodeFrame::Args {
                        pure,
                        op,
                        argc,
                        args: Vec::with_capacity(argc),
                    });
                    None
                }
            }
            TAG_GET => {
                let place = decode_place(r, cfg)?;
                Some(push_node(cfg, Node::Get(place)))
            }
            TAG_SET => {
                if !(allow_set && frames.is_empty()) {
                    return Err(DecodeError::SetNotAllowedHere);
                }
                let place = decode_place(r, cfg)?;
                frames.push(NodeFrame::SetValue { place });
                None
            }
            t => return Err(DecodeError::BadNodeTag(t)),
        };
        let Some(mut id) = completed else { continue };
        // Bubble completed children up through the pending frames.
        loop {
            match frames.last_mut() {
                None => return Ok(id),
                Some(NodeFrame::Args { argc, args, .. }) => {
                    args.push(id);
                    if args.len() < *argc {
                        break;
                    }
                    let Some(NodeFrame::Args { pure, op, args, .. }) = frames.pop() else {
                        unreachable!("just matched an Args frame");
                    };
                    let node = if pure {
                        Node::PureInstr { op, args }
                    } else {
                        Node::Instr { op, args }
                    };
                    id = push_node(cfg, node);
                }
                Some(NodeFrame::SetValue { place }) => {
                    let place = *place;
                    frames.pop();
                    id = push_node(cfg, Node::Set { place, value: id });
                }
            }
        }
    }
}

/// Decodes an encoded frontend-level CFG.
///
/// # Errors
///
/// Returns a [`DecodeError`] for any malformed input: bad magic/version/op count,
/// truncated data, unknown tags or op ids, out-of-range indices, misplaced `Set`
/// nodes, or trailing bytes.
pub fn decode_cfg(data: &[u8]) -> Result<Cfg, DecodeError> {
    let mut r = Reader::new(data);
    if r.bytes(4).map_err(|_| DecodeError::BadMagic)? != MAGIC {
        return Err(DecodeError::BadMagic);
    }
    let version = r.u16()?;
    if version != ENCODING_VERSION {
        return Err(DecodeError::UnsupportedVersion(version));
    }
    let op_count = r.u16()?;
    if op_count != Op::COUNT {
        return Err(DecodeError::OpCountMismatch {
            expected: Op::COUNT,
            found: op_count,
        });
    }

    let mut cfg = Cfg {
        version,
        op_count,
        ..Cfg::default()
    };

    let string_count = r.count("string table")?;
    cfg.strings.reserve(string_count);
    for _ in 0..string_count {
        let len = r.count("string length")?;
        let bytes = r.bytes(len)?;
        let s = std::str::from_utf8(bytes).map_err(|_| DecodeError::InvalidUtf8)?;
        cfg.strings.push(s.to_owned());
    }

    let temp_count = r.count("temp block table")?;
    cfg.temp_blocks.reserve(temp_count);
    for _ in 0..temp_count {
        let name = r.varuint()?;
        let name_idx = usize::try_from(name).map_err(|_| DecodeError::BadStringIndex(name))?;
        if name_idx >= cfg.strings.len() {
            return Err(DecodeError::BadStringIndex(name));
        }
        let size = r.varuint()?;
        cfg.temp_blocks.push(crate::cfg::TempBlockDef {
            name: name_idx,
            size,
        });
    }

    let block_count = r.count("block")?;
    cfg.blocks.reserve(block_count);
    for _ in 0..block_count {
        let stmt_count = r.count("statement")?;
        let mut statements = Vec::with_capacity(stmt_count);
        for _ in 0..stmt_count {
            statements.push(decode_node(&mut r, &mut cfg, true)?);
        }
        let test = decode_node(&mut r, &mut cfg, false)?;
        let edge_count = r.count("edge")?;
        let mut outgoing = Vec::with_capacity(edge_count);
        for _ in 0..edge_count {
            let cond = match r.u8()? {
                COND_NONE => EdgeCond::None,
                COND_INT => EdgeCond::Int(r.varint()?),
                COND_FLOAT => EdgeCond::Float(r.f64()?),
                t => return Err(DecodeError::BadCondTag(t)),
            };
            let target = r.varuint()?;
            let target_idx =
                usize::try_from(target).map_err(|_| DecodeError::BadBlockTarget(target))?;
            if target_idx >= block_count {
                return Err(DecodeError::BadBlockTarget(target));
            }
            outgoing.push(Edge {
                cond,
                target: target_idx,
            });
        }
        cfg.blocks.push(BasicBlock {
            statements,
            test,
            outgoing,
        });
    }

    if r.remaining() != 0 {
        return Err(DecodeError::TrailingBytes);
    }
    Ok(cfg)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cfg::{canonical_dump, cfg_to_text};

    /// Minimal encoder for tests: mirrors the Python encoder's byte layout so the
    /// decoder is exercised against independently-constructed input.
    #[derive(Default)]
    struct TestWriter {
        buf: Vec<u8>,
    }

    impl TestWriter {
        fn header(mut self) -> Self {
            self.buf.extend_from_slice(MAGIC);
            self.buf.extend_from_slice(&ENCODING_VERSION.to_le_bytes());
            self.buf.extend_from_slice(&Op::COUNT.to_le_bytes());
            self
        }

        fn u8(mut self, v: u8) -> Self {
            self.buf.push(v);
            self
        }

        fn u16(mut self, v: u16) -> Self {
            self.buf.extend_from_slice(&v.to_le_bytes());
            self
        }

        fn f64(mut self, v: f64) -> Self {
            self.buf.extend_from_slice(&v.to_le_bytes());
            self
        }

        fn varuint(mut self, mut v: u64) -> Self {
            loop {
                let bits = (v & 0x7f) as u8;
                v >>= 7;
                if v != 0 {
                    self.buf.push(bits | 0x80);
                } else {
                    self.buf.push(bits);
                    return self;
                }
            }
        }

        #[allow(clippy::cast_sign_loss)]
        fn varint(self, v: i64) -> Self {
            self.varuint(((v << 1) ^ (v >> 63)) as u64)
        }

        fn string(mut self, s: &str) -> Self {
            self = self.varuint(s.len() as u64);
            self.buf.extend_from_slice(s.as_bytes());
            self
        }
    }

    /// `{ v0 <- 5; test 0; goto exit }` with one temp block.
    fn tiny_cfg_bytes() -> Vec<u8> {
        TestWriter::default()
            .header()
            .varuint(1) // strings
            .string("v0")
            .varuint(1) // temp blocks
            .varuint(0) // name
            .varuint(1) // size
            .varuint(1) // blocks
            .varuint(1) // statements
            .u8(TAG_SET)
            .u8(BLOCK_TEMP)
            .varuint(0)
            .u8(INDEX_INT)
            .varint(0)
            .varint(0) // offset
            .u8(TAG_CONST_INT)
            .varint(5)
            .u8(TAG_CONST_INT) // test
            .varint(0)
            .varuint(0) // edges
            .buf
    }

    #[test]
    fn decodes_tiny_cfg() {
        let cfg = decode_cfg(&tiny_cfg_bytes()).unwrap();
        assert_eq!(cfg.blocks.len(), 1);
        assert_eq!(cfg.strings, vec!["v0".to_owned()]);
        assert_eq!(
            canonical_dump(&cfg),
            "cfg-canonical v1\n\
             ops 191\n\
             strings 1\n\
             \x20 string 0 \"v0\"\n\
             temps 1\n\
             \x20 temp 0 name=0 size=1\n\
             blocks 1\n\
             block 0\n\
             \x20 stmts 1\n\
             \x20   (set (place b=t:0 i=i:0 o=0) (const i:5))\n\
             \x20 test (const i:0)\n\
             \x20 edges 0\n"
        );
        assert_eq!(cfg_to_text(&cfg), "0:\n  v0 <- 5\n  goto exit\n");
    }

    #[test]
    fn decodes_conditional_and_switch_edges() {
        let bytes = TestWriter::default()
            .header()
            .varuint(0) // strings
            .varuint(0) // temp blocks
            .varuint(3) // blocks
            // block 0: test Get(place 1000[2]), edges 0->1, none->2
            .varuint(0)
            .u8(TAG_GET)
            .u8(BLOCK_INT)
            .varint(1000)
            .u8(INDEX_INT)
            .varint(2)
            .varint(0)
            .varuint(2)
            .u8(COND_INT)
            .varint(0)
            .varuint(1)
            .u8(COND_NONE)
            .varuint(2)
            // block 1: empty, switch edges -1 -> 2, 2.5 -> 2, default -> 0 (self-ish loop back)
            .varuint(0)
            .u8(TAG_CONST_INT)
            .varint(0)
            .varuint(3)
            .u8(COND_INT)
            .varint(-1)
            .varuint(2)
            .u8(COND_FLOAT)
            .f64(2.5)
            .varuint(2)
            .u8(COND_NONE)
            .varuint(0)
            // block 2: exit
            .varuint(0)
            .u8(TAG_CONST_INT)
            .varint(0)
            .varuint(0)
            .buf;
        let cfg = decode_cfg(&bytes).unwrap();
        let text = cfg_to_text(&cfg);
        assert_eq!(
            text,
            "0:\n  goto 2 if 1000[2] else 1\n1:\n  goto when 0\n    -1 -> 2\n    2.5 -> 2\n    default -> 0\n2:\n  goto exit\n"
        );
        let dump = canonical_dump(&cfg);
        assert!(dump.contains("edge f:0x4004000000000000 -> 2"), "{dump}");
        assert!(dump.contains("edge i:-1 -> 2"), "{dump}");
    }

    #[test]
    fn deep_expression_nesting_is_iterative() {
        // Negate(Negate(...(1)...)) nested 200k deep: must not overflow the stack.
        let depth = 200_000;
        let mut w = TestWriter::default()
            .header()
            .varuint(0)
            .varuint(0)
            .varuint(1)
            .varuint(1);
        for _ in 0..depth {
            w = w.u8(TAG_PURE_INSTR).u16(Op::Negate.id()).varuint(1);
        }
        w = w.u8(TAG_CONST_INT).varint(1);
        w = w.u8(TAG_CONST_INT).varint(0); // test
        w = w.varuint(0); // edges
        let cfg = decode_cfg(&w.buf).unwrap();
        assert_eq!(cfg.nodes.len(), depth + 2);
        // The dumps must be iterative too.
        let dump = canonical_dump(&cfg);
        assert!(dump.starts_with("cfg-canonical v1\n"));
        let text = cfg_to_text(&cfg);
        assert!(text.starts_with("0:\n  Negate(Negate("));
    }

    #[test]
    fn deep_place_nesting_is_iterative() {
        // place(block=place(block=...), index=0, offset=0) nested 100k deep.
        let depth = 100_000;
        let mut w = TestWriter::default()
            .header()
            .varuint(0)
            .varuint(0)
            .varuint(1)
            .varuint(1)
            .u8(TAG_GET);
        for _ in 0..depth {
            w = w.u8(BLOCK_PLACE);
        }
        w = w
            .u8(BLOCK_INT)
            .varint(4000)
            .u8(INDEX_INT)
            .varint(0)
            .varint(0);
        for _ in 0..depth {
            w = w.u8(INDEX_INT).varint(0).varint(0);
        }
        w = w.u8(TAG_CONST_INT).varint(0); // test
        w = w.varuint(0); // edges
        let cfg = decode_cfg(&w.buf).unwrap();
        assert_eq!(cfg.places.len(), depth + 1);
        let dump = canonical_dump(&cfg);
        assert!(dump.contains("(get (place b=(place b=(place"));
        let _ = cfg_to_text(&cfg);
    }

    #[test]
    fn rejects_bad_magic() {
        let mut bytes = tiny_cfg_bytes();
        bytes[0] = b'X';
        assert_eq!(decode_cfg(&bytes), Err(DecodeError::BadMagic));
    }

    #[test]
    fn rejects_bad_version() {
        let mut bytes = tiny_cfg_bytes();
        bytes[4] = 99;
        assert_eq!(decode_cfg(&bytes), Err(DecodeError::UnsupportedVersion(99)));
    }

    #[test]
    fn rejects_op_count_mismatch() {
        let mut bytes = tiny_cfg_bytes();
        bytes[6] = 0xff;
        bytes[7] = 0xff;
        assert_eq!(
            decode_cfg(&bytes),
            Err(DecodeError::OpCountMismatch {
                expected: Op::COUNT,
                found: 0xffff,
            })
        );
    }

    #[test]
    fn rejects_truncation_at_every_length() {
        let bytes = tiny_cfg_bytes();
        for len in 0..bytes.len() {
            assert!(
                decode_cfg(&bytes[..len]).is_err(),
                "length {len} should not decode"
            );
        }
    }

    #[test]
    fn rejects_trailing_bytes() {
        let mut bytes = tiny_cfg_bytes();
        bytes.push(0);
        assert_eq!(decode_cfg(&bytes), Err(DecodeError::TrailingBytes));
    }

    #[test]
    fn rejects_bad_op_id() {
        let bytes = TestWriter::default()
            .header()
            .varuint(0)
            .varuint(0)
            .varuint(1)
            .varuint(0)
            .u8(TAG_PURE_INSTR)
            .u16(Op::COUNT) // first invalid id
            .varuint(0)
            .varuint(0)
            .buf;
        assert_eq!(decode_cfg(&bytes), Err(DecodeError::BadOpId(Op::COUNT)));
    }

    #[test]
    fn rejects_impure_op_in_pure_instr() {
        let bytes = TestWriter::default()
            .header()
            .varuint(0)
            .varuint(0)
            .varuint(1)
            .varuint(0)
            .u8(TAG_PURE_INSTR)
            .u16(Op::Set.id())
            .varuint(0)
            .varuint(0)
            .buf;
        assert_eq!(
            decode_cfg(&bytes),
            Err(DecodeError::OpNotPure(Op::Set.id()))
        );
    }

    #[test]
    fn rejects_set_as_test() {
        let bytes = TestWriter::default()
            .header()
            .varuint(0)
            .varuint(0)
            .varuint(1)
            .varuint(0) // no statements
            .u8(TAG_SET) // test = Set -> error
            .buf;
        assert_eq!(decode_cfg(&bytes), Err(DecodeError::SetNotAllowedHere));
    }

    #[test]
    fn rejects_set_as_argument() {
        let bytes = TestWriter::default()
            .header()
            .varuint(0)
            .varuint(0)
            .varuint(1)
            .varuint(1)
            .u8(TAG_INSTR)
            .u16(Op::Execute.id())
            .varuint(1)
            .u8(TAG_SET) // arg = Set -> error
            .buf;
        assert_eq!(decode_cfg(&bytes), Err(DecodeError::SetNotAllowedHere));
    }

    #[test]
    fn rejects_bad_indices_and_tags() {
        // Temp block index out of range.
        let bytes = TestWriter::default()
            .header()
            .varuint(0)
            .varuint(0)
            .varuint(1)
            .varuint(0)
            .u8(TAG_GET)
            .u8(BLOCK_TEMP)
            .varuint(7)
            .buf;
        assert_eq!(decode_cfg(&bytes), Err(DecodeError::BadTempBlockIndex(7)));

        // String index out of range in the temp table.
        let bytes = TestWriter::default()
            .header()
            .varuint(0)
            .varuint(1)
            .varuint(3)
            .varuint(1)
            .buf;
        assert_eq!(decode_cfg(&bytes), Err(DecodeError::BadStringIndex(3)));

        // Edge target out of range.
        let bytes = TestWriter::default()
            .header()
            .varuint(0)
            .varuint(0)
            .varuint(1)
            .varuint(0)
            .u8(TAG_CONST_INT)
            .varint(0)
            .varuint(1)
            .u8(COND_NONE)
            .varuint(1)
            .buf;
        assert_eq!(decode_cfg(&bytes), Err(DecodeError::BadBlockTarget(1)));

        // Unknown tags.
        let base = TestWriter::default()
            .header()
            .varuint(0)
            .varuint(0)
            .varuint(1)
            .varuint(1);
        assert_eq!(decode_cfg(&base.u8(9).buf), Err(DecodeError::BadNodeTag(9)));
    }

    #[test]
    fn rejects_oversized_counts_without_allocating() {
        // A statement count of ~2^60 must error out, not attempt allocation.
        let bytes = TestWriter::default()
            .header()
            .varuint(0)
            .varuint(0)
            .varuint(1)
            .varuint(1 << 60)
            .buf;
        assert_eq!(
            decode_cfg(&bytes),
            Err(DecodeError::CountTooLarge("statement"))
        );
    }

    #[test]
    fn rejects_invalid_utf8_string() {
        let mut w = TestWriter::default().header().varuint(1).varuint(2);
        w.buf.extend_from_slice(&[0xff, 0xfe]);
        let bytes = w.varuint(0).varuint(1).buf;
        assert_eq!(decode_cfg(&bytes), Err(DecodeError::InvalidUtf8));
    }

    #[test]
    fn rejects_varuint_overflow() {
        let mut w = TestWriter::default().header();
        w.buf.extend_from_slice(&[0xff; 10]); // 70 bits of continuation
        assert_eq!(decode_cfg(&w.buf), Err(DecodeError::VaruintOverflow));
    }

    #[test]
    fn nan_payloads_survive_in_canonical_dump() {
        let payload_nan = f64::from_bits(0x7ff8_0000_0000_beef);
        let bytes = TestWriter::default()
            .header()
            .varuint(0)
            .varuint(0)
            .varuint(1)
            .varuint(1)
            .u8(TAG_CONST_FLOAT)
            .f64(payload_nan)
            .u8(TAG_CONST_INT)
            .varint(0)
            .varuint(0)
            .buf;
        let cfg = decode_cfg(&bytes).unwrap();
        assert!(canonical_dump(&cfg).contains("(const f:0x7ff800000000beef)"));
    }
}
