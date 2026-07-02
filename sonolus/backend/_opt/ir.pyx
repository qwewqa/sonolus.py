# cython: language_level=3
"""Arena IR implementation: interning, marshal in/out, verify, debug entry.

See ir.pxd for the data-layout contract and OPTIMIZER_REWRITE.md sections 6.1
and 6.2 for the semantics. Marshal in/out hold the GIL (they touch Python
BasicBlock/IR objects); the flat arena they build/consume is what the M2+ passes
will operate on in nogil regions.
"""

from libc.stdint cimport int16_t, int32_t, uint8_t, uint16_t, uint32_t, uint64_t
from libc.stdlib cimport free, realloc
from libc.math cimport isinf, isnan

from sonolus.backend._opt._ops_gen cimport (
    OPX_CONST,
    OPX_GET,
    OPX_PHI,
    OPX_SET,
    OPX_UNDEF,
    OP_RUNTIME_COUNT,
    OP_TABLE_SIZE,
    SONOLUS_OP_CONTROL_FLOW,
    SONOLUS_OP_NAMES,
    SONOLUS_OP_PURE,
    SONOLUS_OP_SIDE_EFFECTS,
)

from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.ops import Op as _Op
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock
from sonolus.backend.blocks import BlockData
from sonolus.backend.optimize.flow import (
    BasicBlock,
    FlowEdge,
    traverse_cfg_reverse_postorder,
)

# --------------------------------------------------------------------------
# Static op maps (Python side, built once from the same source of truth as the
# generated C table: ops.py declaration order). Used at the marshal boundary.
# --------------------------------------------------------------------------
_OPS = list(_Op)
_OP_TO_ID = {op: i for i, op in enumerate(_OPS)}
_ID_TO_OP = _OPS
_ASSOC_IDS = frozenset(
    {_OP_TO_ID[_Op.Add], _OP_TO_ID[_Op.Multiply], _OP_TO_ID[_Op.Mod], _OP_TO_ID[_Op.Rem]}
)

# Canonical quiet-NaN used so all NaN constants intern to one id.
cdef uint64_t _CANON_NAN_BITS = <uint64_t>0x7FF8000000000000
cdef double _CANON_NAN = 0.0


cdef double _bits_to_double(uint64_t b) noexcept nogil:
    return (<double*>&b)[0]


cdef inline bint _op_pure_c(uint16_t op) noexcept nogil:
    # Reads the generated static const table with no GIL -- the property M2's
    # nogil passes rely on.
    return SONOLUS_OP_PURE[op] != 0


_CANON_NAN = _bits_to_double(_CANON_NAN_BITS)


cdef void* _grow(void* buf, int32_t* cap, int32_t need, size_t elemsize) except NULL:
    """Grow a buffer to hold at least ``need`` elements, doubling capacity."""
    cdef int32_t newcap
    cdef void* p
    if need <= cap[0]:
        return buf
    newcap = cap[0] if cap[0] > 0 else 8
    while newcap < need:
        newcap *= 2
    p = realloc(buf, <size_t>newcap * elemsize)
    if p == NULL:
        raise MemoryError()
    cap[0] = newcap
    return p


def _edge_sort_key(edge):
    # Deterministic edge order; avoids comparing None with numbers and avoids
    # comparing None with None (parallel default edges).
    if edge.cond is None:
        return (1, 0.0)
    return (0, float(edge.cond))


cdef class Func:
    """One per-callback arena. See ir.pxd for the field contract."""

    def __cinit__(self):
        self.instrs = NULL
        self.n_instrs = 0
        self.cap_instrs = 0
        self.args = NULL
        self.n_args = 0
        self.cap_args = 0
        self.blocks = NULL
        self.n_blocks = 0
        self.cap_blocks = 0
        self.edges = NULL
        self.n_edges = 0
        self.cap_edges = 0
        self.consts = NULL
        self.n_consts = 0
        self.cap_consts = 0
        self.places = NULL
        self.n_places = 0
        self.cap_places = 0
        self.temps = NULL
        self.n_temps = 0
        self.cap_temps = 0
        self.entry_block = 0
        self.is_ssa = False
        self.undef_val = -1
        self._ssa_undef = None
        self.names = []
        self._const_intern = {}
        self._temp_intern = {}
        self._place_intern = {}
        self.blocks_type = None
        self.callback = None
        self._block_enum_by_id = {}
        self._block_map = {}

    def __dealloc__(self):
        free(self.instrs)
        free(self.args)
        free(self.blocks)
        free(self.edges)
        free(self.consts)
        free(self.places)
        free(self.temps)

    # -- growable-buffer allocation helpers --------------------------------

    cdef int32_t _alloc_instr(self) except -1:
        self.instrs = <Instr*>_grow(<void*>self.instrs, &self.cap_instrs, self.n_instrs + 1, sizeof(Instr))
        cdef int32_t i = self.n_instrs
        self.n_instrs += 1
        return i

    cdef int32_t _emit(self, uint16_t op, uint8_t flags, int32_t block, int32_t aux, list arg_vids) except -1:
        cdef int32_t nargs = len(arg_vids)
        cdef int32_t astart = self.n_args
        cdef int32_t k
        for k in range(nargs):
            self.args = <uint32_t*>_grow(<void*>self.args, &self.cap_args, self.n_args + 1, sizeof(uint32_t))
            self.args[self.n_args] = <uint32_t>(<int32_t>arg_vids[k])
            self.n_args += 1
        cdef int32_t iid = self._alloc_instr()
        self.instrs[iid].op = op
        self.instrs[iid].flags = flags
        self.instrs[iid].block = block
        self.instrs[iid].arg_start = astart
        self.instrs[iid].nargs = <int16_t>nargs
        self.instrs[iid].aux = aux
        return iid

    # -- interning ---------------------------------------------------------

    cdef int32_t _intern_const(self, double d) except -1:
        cdef uint64_t bits
        if isnan(d):
            d = _CANON_NAN
            bits = _CANON_NAN_BITS
        else:
            bits = (<uint64_t*>&d)[0]
        key = int(bits)
        cached = self._const_intern.get(key)
        if cached is not None:
            return <int32_t>cached
        cdef int32_t cid = self.n_consts
        self.consts = <double*>_grow(<void*>self.consts, &self.cap_consts, self.n_consts + 1, sizeof(double))
        self.consts[cid] = d
        self.n_consts += 1
        self._const_intern[key] = cid
        return cid

    cdef int32_t _intern_temp(self, object temp) except -1:
        key = (temp.name, temp.size)
        cached = self._temp_intern.get(key)
        if cached is not None:
            return <int32_t>cached
        cdef int32_t tid = self.n_temps
        cdef int32_t name_id = len(self.names)
        self.names.append(temp.name)
        self.temps = <TempInfo*>_grow(<void*>self.temps, &self.cap_temps, self.n_temps + 1, sizeof(TempInfo))
        self.temps[tid].name_id = name_id
        self.temps[tid].size = <int32_t>temp.size
        self.n_temps += 1
        self._temp_intern[key] = tid
        return tid

    cdef bint _writable_for_block(self, object member) except -1:
        # member: a resolved BlockData (has .writable) or None (unresolved/dynamic).
        if member is None:
            return True
        if self.callback is None:
            return False
        return self.callback in member.writable

    cdef int32_t _intern_place(self, object place, int32_t block_id) except -1:
        if isinstance(place, SSAPlace):
            raise ValueError("SSA places are not valid marshal-in input (input must not be SSA)")
        if not isinstance(place, BlockPlace):
            raise ValueError(f"Unsupported place: {type(place).__name__}: {place!r}")

        block = place.block
        index = place.index
        cdef int32_t offset = <int32_t>place.offset

        cdef uint8_t kind
        cdef uint8_t flags = 0
        cdef int32_t block_ref
        cdef int32_t block_id_int
        cdef int32_t index_val
        cdef int32_t off = offset
        resolved_member = None

        if isinstance(block, TempBlock):
            block_ref = self._intern_temp(block)
            if block.size == 0:
                kind = PLACE_TEMP_SIZE0
            elif block.size == 1:
                kind = PLACE_TEMP_SCALAR
            else:
                kind = PLACE_TEMP_ARRAY
            flags |= PLACE_WRITABLE
        elif isinstance(block, (BlockPlace, IRPureInstr, IRInstr, IRGet)):
            # Pointer dereference: the block id is computed at runtime.
            block_ref = self._value_of(block, block_id)
            kind = PLACE_DYNAMIC_BLOCK
            flags |= PLACE_WRITABLE
        elif isinstance(block, (int, float)) and not isinstance(block, bool):
            kind = PLACE_REAL_BLOCK
            block_id_int = <int32_t>int(block)
            block_ref = block_id_int
            if isinstance(block, BlockData):
                resolved_member = block
                flags |= PLACE_BLOCK_IS_ENUM
                self._block_enum_by_id[block_id_int] = block
            else:
                # Raw int: resolve against the mode (subsumes NormalizeBlocks) for
                # writability only; display stays a raw int.
                if self.blocks_type is not None:
                    resolved_member = self._block_map.get(block_id_int)
            if self._writable_for_block(resolved_member):
                flags |= PLACE_WRITABLE
        else:
            raise ValueError(f"Unsupported block value: {type(block).__name__}: {block!r}")

        # Index: fold a constant integer index into the offset; keep dynamic
        # indices as a value id.
        if isinstance(index, bool):
            raise ValueError(f"Unsupported index: {index!r}")
        if isinstance(index, int):
            index_val = -1
            off = offset + <int32_t>index
        elif isinstance(index, IRConst):
            v = index.value
            if isinstance(v, int) and not isinstance(v, bool):
                index_val = -1
                off = offset + <int32_t>v
            elif isinstance(v, float) and (not isinf(v)) and (not isnan(v)) and v.is_integer():
                index_val = -1
                off = offset + <int32_t>v
            else:
                index_val = self._value_of(index, block_id)
                off = offset
        else:
            index_val = self._value_of(index, block_id)
            off = offset

        key = (kind, block_ref, index_val, off, flags)
        cached = self._place_intern.get(key)
        if cached is not None:
            return <int32_t>cached
        cdef int32_t pid = self.n_places
        self.places = <PlaceInfo*>_grow(<void*>self.places, &self.cap_places, self.n_places + 1, sizeof(PlaceInfo))
        self.places[pid].kind = kind
        self.places[pid].flags = flags
        self.places[pid].block_ref = block_ref
        self.places[pid].index_val = index_val
        self.places[pid].offset = off
        self.n_places += 1
        self._place_intern[key] = pid
        return pid

    # -- statement / value expansion (marshal in) --------------------------

    cdef int32_t _value_of(self, object node, int32_t block_id) except -1:
        if isinstance(node, IRConst):
            return self._emit_const(node.value, block_id)
        if isinstance(node, IRPureInstr):
            return self._emit_pure(node, block_id)
        if isinstance(node, IRInstr):
            return self._emit_impure(node, block_id)
        if isinstance(node, IRGet):
            pid = self._intern_place(node.place, block_id)
            return self._emit(OPX_GET, FLAG_PINNED, block_id, pid, [])
        if isinstance(node, BlockPlace):
            # Bare place used as a value (an index/block-position read).
            pid = self._intern_place(node, block_id)
            return self._emit(OPX_GET, FLAG_PINNED, block_id, pid, [])
        if isinstance(node, (int, float)) and not isinstance(node, bool):
            return self._emit_const(node, block_id)
        raise ValueError(f"Unsupported IR value: {type(node).__name__}: {node!r}")

    cdef int32_t _emit_const(self, object value, int32_t block_id) except -1:
        cdef uint8_t flags = FLAG_PURE
        if isinstance(value, int) and not isinstance(value, bool):
            flags |= FLAG_CONST_IS_INT
        cdef int32_t cid = self._intern_const(<double>float(value))
        return self._emit(OPX_CONST, flags, block_id, cid, [])

    cdef int32_t _emit_pure(self, object node, int32_t block_id) except -1:
        cdef int32_t op_id = _OP_TO_ID[node.op]
        args = node.args
        cdef int32_t n = len(args)
        cdef int32_t fold, a, k
        if op_id in _ASSOC_IDS and n > 2:
            # Defensively binarize associative n-ary input left-to-right.
            fold = self._value_of(args[0], block_id)
            for k in range(1, n):
                a = self._value_of(args[k], block_id)
                fold = self._emit(<uint16_t>op_id, FLAG_PURE, block_id, -1, [fold, a])
            return fold
        arg_vids = [self._value_of(args[k], block_id) for k in range(n)]
        return self._emit(<uint16_t>op_id, FLAG_PURE, block_id, -1, arg_vids)

    cdef int32_t _emit_impure(self, object node, int32_t block_id) except -1:
        cdef int32_t op_id = _OP_TO_ID[node.op]
        arg_vids = [self._value_of(a, block_id) for a in node.args]
        cdef uint8_t flags = FLAG_PINNED
        if node.op.side_effects:
            flags |= FLAG_SIDE_EFFECT
        return self._emit(<uint16_t>op_id, flags, block_id, -1, arg_vids)

    cdef int _emit_stmt(self, object stmt, int32_t block_id) except -1:
        cdef int32_t vid, pid
        if isinstance(stmt, IRSet):
            vid = self._value_of(stmt.value, block_id)
            pid = self._intern_place(stmt.place, block_id)
            self._emit(OPX_SET, FLAG_SIDE_EFFECT | FLAG_PINNED | FLAG_STMT_ROOT, block_id, pid, [vid])
        elif isinstance(stmt, IRInstr):
            # Bare side-effecting statement.
            vid = self._value_of(stmt, block_id)
            self.instrs[vid].flags |= FLAG_STMT_ROOT
        else:
            raise ValueError(
                f"Unsupported statement: {type(stmt).__name__}: {stmt!r} "
                f"(expected IRSet or a bare side-effecting IRInstr)"
            )
        return 0

    cdef int _push_edge(self, int32_t src, int32_t dst, object cond) except -1:
        self.edges = <Edge*>_grow(<void*>self.edges, &self.cap_edges, self.n_edges + 1, sizeof(Edge))
        cdef int32_t ei = self.n_edges
        self.edges[ei].src = src
        self.edges[ei].dst = dst
        if cond is None:
            self.edges[ei].cond_kind = EDGE_COND_NONE
            self.edges[ei].cond_is_int = 0
            self.edges[ei].cond = 0.0
        else:
            self.edges[ei].cond_kind = EDGE_COND_VALUE
            self.edges[ei].cond_is_int = 1 if (isinstance(cond, int) and not isinstance(cond, bool)) else 0
            self.edges[ei].cond = <double>float(cond)
        self.n_edges += 1
        return 0

    cdef int _marshal(self, object entry, object mode, object callback) except -1:
        self.callback = callback
        if mode is not None:
            self.blocks_type = mode.blocks
            self._block_map = {int(m): m for m in self.blocks_type}
        else:
            self.blocks_type = None
            self._block_map = {}

        rpo_blocks = list(traverse_cfg_reverse_postorder(entry))
        cdef int32_t nb = len(rpo_blocks)
        cdef int32_t i
        if nb == 0:
            raise ValueError("Empty CFG")
        self.blocks = <BlockInfo*>_grow(<void*>self.blocks, &self.cap_blocks, nb, sizeof(BlockInfo))
        self.n_blocks = nb
        self.entry_block = 0

        block_id = {b: i for i, b in enumerate(rpo_blocks)}

        cdef int32_t bid, tv, istart
        for bid in range(nb):
            pyb = rpo_blocks[bid]
            if pyb.phis:
                raise ValueError("block.phis must be empty on marshal-in (input is not SSA)")
            istart = self.n_instrs
            self.blocks[bid].instr_start = istart
            self.blocks[bid].phi_start = 0
            self.blocks[bid].phi_count = 0
            self.blocks[bid].rpo = bid
            self.blocks[bid].idom = -1
            for stmt in pyb.statements:
                self._emit_stmt(stmt, bid)
            tv = self._value_of(pyb.test, bid)
            self.blocks[bid].test_val = tv
            self.blocks[bid].instr_count = self.n_instrs - istart
            self.blocks[bid].edge_start = self.n_edges
            for e in sorted(pyb.outgoing, key=_edge_sort_key):
                self._push_edge(bid, <int32_t>block_id[e.dst], e.cond)
            self.blocks[bid].edge_count = self.n_edges - self.blocks[bid].edge_start
        return 0

    # -- marshal out -------------------------------------------------------

    cdef object _make_const(self, double d, bint is_int):
        cdef object pyd = d
        if is_int and (not isinf(d)) and (not isnan(d)) and pyd.is_integer():
            return IRConst(int(pyd))
        return IRConst(pyd)

    cdef object _export_place(self, int32_t pid, dict names):
        cdef uint8_t kind = self.places[pid].kind
        cdef uint8_t flags = self.places[pid].flags
        cdef int32_t block_ref = self.places[pid].block_ref
        cdef int32_t index_val = self.places[pid].index_val
        cdef int32_t offset = self.places[pid].offset
        if kind == PLACE_TEMP_SCALAR or kind == PLACE_TEMP_ARRAY or kind == PLACE_TEMP_SIZE0:
            block_obj = TempBlock(names[block_ref], self.temps[block_ref].size)
        elif kind == PLACE_REAL_BLOCK:
            if flags & PLACE_BLOCK_IS_ENUM:
                block_obj = self._block_enum_by_id[block_ref]
            else:
                block_obj = block_ref
        elif kind == PLACE_DYNAMIC_BLOCK:
            block_obj = self._export_value(block_ref, names, True)
        else:
            raise AssertionError("bad place kind")
        if index_val < 0:
            return BlockPlace(block_obj, offset, 0)
        index_obj = self._export_value(index_val, names, True)
        return BlockPlace(block_obj, index_obj, offset)

    cdef object _export_value(self, int32_t vid, dict names, bint as_read_place):
        cdef uint16_t op = self.instrs[vid].op
        cdef int32_t astart, k, nargs
        if op == OPX_CONST:
            return self._make_const(self.consts[self.instrs[vid].aux], self.instrs[vid].flags & FLAG_CONST_IS_INT)
        if op == OPX_GET:
            place_obj = self._export_place(self.instrs[vid].aux, names)
            if as_read_place:
                return place_obj
            return IRGet(place_obj)
        if op == OPX_SET:
            raise AssertionError("OPX_SET encountered in value position")
        op_member = _ID_TO_OP[op]
        astart = self.instrs[vid].arg_start
        nargs = self.instrs[vid].nargs
        arg_objs = [self._export_value(<int32_t>self.args[astart + k], names, False) for k in range(nargs)]
        if op_member.pure:
            return IRPureInstr(op_member, arg_objs)
        return IRInstr(op_member, arg_objs)

    cdef object _export_stmt(self, int32_t i, dict names):
        cdef uint16_t op = self.instrs[i].op
        if op == OPX_SET:
            place_obj = self._export_place(self.instrs[i].aux, names)
            value = self._export_value(<int32_t>self.args[self.instrs[i].arg_start], names, False)
            return IRSet(place_obj, value)
        return self._export_value(i, names, False)

    cdef _assign_temp_names(self, dict names):
        cdef int32_t counter = 0
        cdef int32_t i, pid, kind, tid
        cdef uint16_t op
        for i in range(self.n_instrs):
            op = self.instrs[i].op
            if op == OPX_GET or op == OPX_SET:
                pid = self.instrs[i].aux
                kind = self.places[pid].kind
                if kind == PLACE_TEMP_SCALAR or kind == PLACE_TEMP_ARRAY or kind == PLACE_TEMP_SIZE0:
                    tid = self.places[pid].block_ref
                    if tid not in names:
                        names[tid] = f"v{counter}"
                        counter += 1

    def _export(self):
        if self.is_ssa:
            return self._export_ssa()
        cdef int32_t nb = self.n_blocks
        cdef int32_t bid, i, istart, icount, estart, ecount, tv
        cdef object cond
        py_blocks = [BasicBlock() for _ in range(nb)]
        names = {}
        self._assign_temp_names(names)

        for bid in range(nb):
            blk = py_blocks[bid]
            istart = self.blocks[bid].instr_start
            icount = self.blocks[bid].instr_count
            stmts = []
            for i in range(istart, istart + icount):
                if self.instrs[i].flags & FLAG_STMT_ROOT:
                    stmts.append(self._export_stmt(i, names))
            blk.statements = stmts
            tv = self.blocks[bid].test_val
            if tv >= 0:
                blk.test = self._export_value(tv, names, False)
            else:
                blk.test = IRConst(0)

        self._wire_edges(py_blocks)
        return py_blocks[self.entry_block]

    def _wire_edges(self, list py_blocks):
        cdef int32_t bid, i, estart, ecount
        cdef object cond
        for bid in range(self.n_blocks):
            estart = self.blocks[bid].edge_start
            ecount = self.blocks[bid].edge_count
            for i in range(estart, estart + ecount):
                if self.edges[i].cond_kind == EDGE_COND_VALUE:
                    if self.edges[i].cond_is_int:
                        cond = int(self.edges[i].cond)
                    else:
                        cond = float(self.edges[i].cond)
                else:
                    cond = None
                src_b = py_blocks[self.edges[i].src]
                dst_b = py_blocks[self.edges[i].dst]
                edge = FlowEdge(src_b, dst_b, cond)
                src_b.outgoing.add(edge)
                dst_b.incoming.add(edge)

    # ------------------------------------------------------------------
    # SSA-form export (inspection only; the ["cfg_cleanup","ssa"] debug path).
    #
    # Value-based SSA has cross-block value edges and per-edge phi operands that
    # the non-SSA BasicBlock IR cannot represent directly, so this fully
    # materializes every value (except inlined constants and the shared UNDEF)
    # as an ``SSAPlace`` and emits shallow three-address statements. It does NOT
    # round-trip through marshal_in (which rejects SSA); it exists so
    # ``cfg_to_text`` can render the SSA form. Out-of-SSA consumes the arena
    # directly, not this export.
    # ------------------------------------------------------------------

    def _export_ssa(self):
        cdef int32_t nb = self.n_blocks
        cdef int32_t bid, i, istart, icount, tv
        cdef uint16_t op

        py_blocks = [BasicBlock() for _ in range(nb)]

        # Incoming-edge lists in the contract order (ascending edge index).
        incoming = [[] for _ in range(nb)]
        for i in range(self.n_edges):
            incoming[self.edges[i].dst].append(i)

        # Assign an SSAPlace to every value that needs one: every non-root,
        # non-const, non-undef instruction (phis included). Deterministic id.
        place_of = {}
        counter = 0
        for bid in range(nb):
            istart = self.blocks[bid].instr_start
            icount = self.blocks[bid].instr_count
            for i in range(istart, istart + icount):
                op = self.instrs[i].op
                if op == OPX_CONST or op == OPX_UNDEF:
                    continue
                if self.instrs[i].flags & FLAG_STMT_ROOT:
                    continue
                if op == OPX_PHI:
                    nm = self.names[self.temps[self.instrs[i].aux].name_id]
                else:
                    nm = "v"
                place_of[i] = SSAPlace(nm, counter)
                counter += 1

        for bid in range(nb):
            blk = py_blocks[bid]
            blk.phis = self._export_phis(bid, place_of, py_blocks, incoming)
            istart = self.blocks[bid].instr_start
            icount = self.blocks[bid].instr_count
            stmts = []
            for i in range(istart, istart + icount):
                op = self.instrs[i].op
                if op == OPX_CONST or op == OPX_UNDEF or op == OPX_PHI:
                    continue
                if self.instrs[i].flags & FLAG_STMT_ROOT:
                    if op == OPX_SET:
                        place_obj = self._ssa_export_place(self.instrs[i].aux, place_of)
                        value = self._ssa_ref(<int32_t>self.args[self.instrs[i].arg_start], place_of)
                        stmts.append(IRSet(place_obj, value))
                    else:
                        stmts.append(self._ssa_build_op(i, place_of))
                else:
                    stmts.append(IRSet(place_of[i], self._ssa_build_tree(i, place_of)))
            blk.statements = stmts
            tv = self.blocks[bid].test_val
            if tv >= 0:
                blk.test = self._ssa_ref(tv, place_of)
            else:
                blk.test = IRConst(0)

        self._wire_edges(py_blocks)
        return py_blocks[self.entry_block]

    def _ssa_build_tree(self, int32_t vid, dict place_of):
        # Shallow RHS tree for a materialized value: operands referenced by place.
        cdef uint16_t op = self.instrs[vid].op
        if op == OPX_GET:
            return IRGet(self._ssa_export_place(self.instrs[vid].aux, place_of))
        return self._ssa_build_op(vid, place_of)

    def _ssa_build_op(self, int32_t vid, dict place_of):
        cdef uint16_t op = self.instrs[vid].op
        cdef int32_t astart = self.instrs[vid].arg_start
        cdef int32_t nargs = self.instrs[vid].nargs
        cdef int32_t k
        op_member = _ID_TO_OP[op]
        arg_objs = [self._ssa_ref(<int32_t>self.args[astart + k], place_of) for k in range(nargs)]
        if op_member.pure:
            return IRPureInstr(op_member, arg_objs)
        return IRInstr(op_member, arg_objs)

    def _ssa_ref(self, int32_t vid, dict place_of):
        # A value in read/operand position -> an IR expression.
        cdef uint16_t op = self.instrs[vid].op
        if op == OPX_CONST:
            return self._make_const(self.consts[self.instrs[vid].aux], self.instrs[vid].flags & FLAG_CONST_IS_INT)
        if op == OPX_UNDEF:
            return IRGet(SSAPlace("undef", 0))
        return IRGet(place_of[vid])

    def _ssa_phi_src(self, int32_t vid, dict place_of):
        # A value as a phi operand -> a place (or an inlined constant).
        cdef uint16_t op = self.instrs[vid].op
        if op == OPX_CONST:
            return self._make_const(self.consts[self.instrs[vid].aux], self.instrs[vid].flags & FLAG_CONST_IS_INT)
        if op == OPX_UNDEF:
            return SSAPlace("undef", 0)
        return place_of[vid]

    def _ssa_export_place(self, int32_t pid, dict place_of):
        cdef uint8_t kind = self.places[pid].kind
        cdef uint8_t flags = self.places[pid].flags
        cdef int32_t block_ref = self.places[pid].block_ref
        cdef int32_t index_val = self.places[pid].index_val
        cdef int32_t offset = self.places[pid].offset
        if kind == PLACE_TEMP_SCALAR or kind == PLACE_TEMP_ARRAY or kind == PLACE_TEMP_SIZE0:
            block_obj = TempBlock(self.names[self.temps[block_ref].name_id], self.temps[block_ref].size)
        elif kind == PLACE_REAL_BLOCK:
            if flags & PLACE_BLOCK_IS_ENUM:
                block_obj = self._block_enum_by_id[block_ref]
            else:
                block_obj = block_ref
        elif kind == PLACE_DYNAMIC_BLOCK:
            block_obj = self._ssa_ref(block_ref, place_of)
        else:
            raise AssertionError("bad place kind")
        if index_val < 0:
            return BlockPlace(block_obj, offset, 0)
        return BlockPlace(block_obj, self._ssa_ref(index_val, place_of), offset)

    def _export_phis(self, int32_t bid, dict place_of, list py_blocks, list incoming):
        # Phi operands are stored per incoming edge (the contract order); normalize
        # to the Python per-predecessor format, checking the equal-operand invariant
        # for parallel edges from the same predecessor.
        inc = incoming[bid]
        result = {}
        cdef int32_t pi, k, astart, nargs
        cdef int32_t pstart = self.blocks[bid].phi_start
        cdef int32_t pcount = self.blocks[bid].phi_count
        for pi in range(pstart, pstart + pcount):
            astart = self.instrs[pi].arg_start
            nargs = self.instrs[pi].nargs
            if nargs != len(inc):
                raise AssertionError("phi arity does not match incoming edge count")
            per_pred = {}
            for k in range(nargs):
                src_block = py_blocks[self.edges[inc[k]].src]
                operand = self._ssa_phi_src(<int32_t>self.args[astart + k], place_of)
                if src_block in per_pred and per_pred[src_block] != operand:
                    raise ValueError("parallel edges from the same predecessor carry unequal phi operands")
                per_pred[src_block] = operand
            result[place_of[pi]] = per_pred
        return result

    def _dom(self, int32_t a, int32_t b):
        # a dominates b (reflexive), via the idom chain (filled by
        # compute_dominators; entry's idom is entry). Debug-only (verify).
        cdef int32_t r = b
        cdef int32_t nr
        while True:
            if r == a:
                return True
            if r == self.entry_block:
                return False
            nr = self.blocks[r].idom
            if nr < 0 or nr == r:
                return False
            r = nr

    # -- python-callable API ----------------------------------------------

    def verify(self):
        """Check arena invariants; raises AssertionError on violation.

        Always available (used by debug_run and the tests); the release build
        does not call it on the hot marshal path. Structural checks run for both
        forms; the def/use ordering check is form-dependent (``is_ssa``).
        """
        cdef int32_t i, b, k, a, astart, nargs, istart, icount, estart, ecount, pid, tv, iv, inc, pi
        cdef bint ssa = self.is_ssa
        cdef uint16_t op
        cdef uint8_t kind
        cdef set uw = self._ssa_undef if self._ssa_undef is not None else set()
        for i in range(self.n_instrs):
            b = self.instrs[i].block
            assert 0 <= b < self.n_blocks, f"instr {i}: block {b} out of range"
            astart = self.instrs[i].arg_start
            nargs = self.instrs[i].nargs
            assert nargs >= 0, f"instr {i}: negative nargs"
            assert astart + nargs <= self.n_args, f"instr {i}: args slice out of range"
            op = self.instrs[i].op
            if op == OPX_PHI:
                assert ssa, f"instr {i}: OPX_PHI outside SSA form"
                # Phi operands may reference later instrs (back edges); each
                # operand's def dominates the corresponding predecessor's exit.
                self._verify_phi(i)
            else:
                for k in range(nargs):
                    a = <int32_t>self.args[astart + k]
                    assert 0 <= a < self.n_instrs, f"instr {i}: arg {a} out of range"
                    assert a < i, f"instr {i}: uses later value {a} (def-before-use)"
                    if ssa:
                        # cross-block operands allowed if the def dominates the use
                        # (UNDEF-widened values are exempt: dead-path relaxation).
                        assert self._dom(self.instrs[a].block, b) or a in uw, (
                            f"instr {i}: arg {a} def does not dominate use"
                        )
                    else:
                        assert self.instrs[a].block == b, f"instr {i}: arg {a} from another block"
            if op == OPX_CONST:
                assert 0 <= self.instrs[i].aux < self.n_consts, f"instr {i}: const id out of range"
            elif op == OPX_GET or op == OPX_SET:
                assert 0 <= self.instrs[i].aux < self.n_places, f"instr {i}: place id out of range"
            elif op == OPX_PHI:
                assert 0 <= self.instrs[i].aux < self.n_temps, f"instr {i}: phi temp out of range"
            elif op == OPX_UNDEF:
                assert ssa, f"instr {i}: OPX_UNDEF outside SSA form"
            else:
                assert op < OP_RUNTIME_COUNT, f"instr {i}: unexpected synthetic op {op}"
                # The FLAG_PURE bit must agree with the static op-metadata table.
                assert bool(self.instrs[i].flags & FLAG_PURE) == _op_pure_c(op), f"instr {i}: purity flag mismatch"
        for b in range(self.n_blocks):
            istart = self.blocks[b].instr_start
            icount = self.blocks[b].instr_count
            assert istart + icount <= self.n_instrs, f"block {b}: instr slice out of range"
            for i in range(istart, istart + icount):
                assert self.instrs[i].block == b, f"block {b}: instr {i} claims another block"
            tv = self.blocks[b].test_val
            assert tv == -1 or (0 <= tv < self.n_instrs), f"block {b}: bad test value"
            estart = self.blocks[b].edge_start
            ecount = self.blocks[b].edge_count
            assert estart + ecount <= self.n_edges, f"block {b}: edge slice out of range"
            for i in range(estart, estart + ecount):
                assert self.edges[i].src == b, f"block {b}: edge {i} has wrong src"
                assert 0 <= self.edges[i].dst < self.n_blocks, f"block {b}: edge {i} bad dst"
                assert self.edges[i].cond_kind == EDGE_COND_NONE or self.edges[i].cond_kind == EDGE_COND_VALUE
            # Phi bookkeeping: phi_count leading OPX_PHI instrs at phi_start; each
            # phi has exactly one operand per incoming edge.
            pi = self.blocks[b].phi_start
            assert self.blocks[b].phi_count >= 0
            if self.blocks[b].phi_count > 0:
                assert ssa, f"block {b}: phis outside SSA form"
                inc = 0
                for i in range(self.n_edges):
                    if self.edges[i].dst == b:
                        inc += 1
                for i in range(pi, pi + self.blocks[b].phi_count):
                    assert self.instrs[i].op == OPX_PHI, f"block {b}: phi slice not all OPX_PHI"
                    assert self.instrs[i].nargs == inc, f"block {b}: phi arity mismatch"
        for pid in range(self.n_places):
            kind = self.places[pid].kind
            if kind == PLACE_TEMP_SCALAR or kind == PLACE_TEMP_ARRAY or kind == PLACE_TEMP_SIZE0:
                assert 0 <= self.places[pid].block_ref < self.n_temps, f"place {pid}: temp id out of range"
            elif kind == PLACE_DYNAMIC_BLOCK:
                assert 0 <= self.places[pid].block_ref < self.n_instrs, f"place {pid}: block value out of range"
            iv = self.places[pid].index_val
            assert iv == -1 or (0 <= iv < self.n_instrs), f"place {pid}: bad index value"
        return True

    def _verify_phi(self, int32_t i):
        # Each phi operand def dominates the corresponding predecessor's exit
        # (UNDEF-widened operands exempt: dead-path relaxation).
        cdef set uw = self._ssa_undef if self._ssa_undef is not None else set()
        cdef int32_t b = self.instrs[i].block
        cdef int32_t astart = self.instrs[i].arg_start
        cdef int32_t nargs = self.instrs[i].nargs
        cdef int32_t k, a, ei
        # incoming edges of b in ascending edge-index order (the contract).
        inc = [ei for ei in range(self.n_edges) if self.edges[ei].dst == b]
        assert len(inc) == nargs, f"instr {i}: phi arity != incoming edges"
        for k in range(nargs):
            a = <int32_t>self.args[astart + k]
            assert 0 <= a < self.n_instrs, f"instr {i}: phi operand {a} out of range"
            assert self._dom(self.instrs[a].block, self.edges[inc[k]].src) or a in uw, (
                f"instr {i}: phi operand {a} does not dominate predecessor exit"
            )

    def stats(self):
        """Return arena element counts (for tests / marshal_stats)."""
        cdef int32_t i
        cdef int32_t nphi = 0
        for i in range(self.n_instrs):
            if self.instrs[i].op == OPX_PHI:
                nphi += 1
        return {
            "instrs": self.n_instrs,
            "blocks": self.n_blocks,
            "edges": self.n_edges,
            "consts": self.n_consts,
            "places": self.n_places,
            "temps": self.n_temps,
            "args": self.n_args,
            "phis": nphi,
        }

    def intern_const(self, value):
        """Intern a numeric constant, returning its const id (test/kernel API)."""
        return self._intern_const(<double>float(value))

    def get_const(self, int cid):
        """Read back an interned constant as a Python float (bit-exact)."""
        if cid < 0 or cid >= self.n_consts:
            raise IndexError("const id out of range")
        return self.consts[cid]


# --------------------------------------------------------------------------
# Op-table introspection (proves the generated static C table is readable and
# lets test_ops_sync cross-check the *compiled* table against ops.py).
# --------------------------------------------------------------------------

def op_table_size():
    return OP_TABLE_SIZE


def op_runtime_count():
    return OP_RUNTIME_COUNT


def op_table_entry(int op_id):
    if op_id < 0 or op_id >= OP_TABLE_SIZE:
        raise IndexError("op id out of range")
    cdef const char* nm = SONOLUS_OP_NAMES[op_id]
    return (
        (<bytes>nm).decode("ascii"),
        <int>SONOLUS_OP_PURE[op_id],
        <int>SONOLUS_OP_SIDE_EFFECTS[op_id],
        <int>SONOLUS_OP_CONTROL_FLOW[op_id],
    )


# --------------------------------------------------------------------------
# Marshal / debug entry points (Python-visible).
# --------------------------------------------------------------------------

def marshal_in(entry, mode=None, callback=None):
    """Marshal a Python BasicBlock CFG into a fresh arena ``Func`` (GIL held)."""
    cdef Func func = Func()
    func._marshal(entry, mode, callback)
    return func


def to_basic_blocks(func):
    """Export an arena ``Func`` back to a fresh Python BasicBlock CFG."""
    if not isinstance(func, Func):
        raise TypeError("expected a Func arena")
    return (<Func>func)._export()


# Debug phase registry: name -> callable(Func) -> Func. Populated by the driver
# module on import (see driver.pyx); debug_run consults it below.
_PHASE_REGISTRY = {}


def register_phase(name, fn):
    """Register a named debug phase (``Func -> Func``) for :func:`debug_run`."""
    _PHASE_REGISTRY[name] = fn


def debug_run(entry, mode=None, callback=None, phases=None):
    """Marshal in, run the named registry phases in order, export back (10 debug API)."""
    cdef Func func = Func()
    func._marshal(entry, mode, callback)
    func.verify()
    if phases:
        # The driver registers the phases on import; import it lazily (it cimports
        # ir, so importing at ir load time would be a cycle) to populate the table.
        if not _PHASE_REGISTRY:
            from sonolus.backend._opt import driver as _driver  # noqa: F401
        for ph in phases:
            fn = _PHASE_REGISTRY.get(ph)
            if fn is None:
                raise ValueError(f"Unknown optimizer phase {ph!r}")
            func = fn(func)
            func.verify()
    return func._export()


def marshal_stats(entry, mode=None, callback=None):
    """Marshal in and return arena element counts (does not export)."""
    cdef Func func = Func()
    func._marshal(entry, mode, callback)
    return func.stats()
