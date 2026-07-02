# cython: language_level=3
"""Arena IR for the Cython optimizer core (milestone M1 foundation).

This ``.pxd`` is the load-bearing contract three later agent waves build on
(mid-end SSA/SCCP/GVN, lowering/allocation, emission). It declares the flat,
arena-allocated IR described in OPTIMIZER_REWRITE.md section 6.1 and the C
layout the ``nogil`` passes read directly.

================================================================================
DESIGN SUMMARY (read before extending)
================================================================================

One ``Func`` arena per callback. No global mutable state (op metadata is the
static ``const`` table in ``_ops_gen``), so per-callback threading is safe by
construction. All hot state is flat C arrays of integer ids; the only Python
objects (``names`` and the interning dicts) are touched at the marshal
boundaries under the GIL and never inside ``nogil`` passes.

Everything is index-addressed:

* a *value* is an instruction; its value-id is its index into ``instrs``.
* a *block* is its index into ``blocks``.
* a *place*/``const``/``temp``/``edge`` is its index into the matching array.

------------------------------------------------------------------------------
Instr (AoS)               one array; per-block slice == the initial schedule
------------------------------------------------------------------------------
* op        u16   opcode: a runtime ``Op`` id (0..OP_RUNTIME_COUNT-1, ==
                  ops.py declaration order) or a synthetic ``OPX_*`` id.
* flags     u8    FLAG_* bitset (pure/side_effect/pinned/dead/stmt_root/
                  const_is_int).
* block     i32   owning block id.
* arg_start i32   start index into ``args`` (operand value-ids).
* nargs     i16   operand count.
* aux       i32   op-specific: OPX_CONST -> const id; OPX_GET/OPX_SET ->
                  place id; runtime ops -> unused (-1).

M1 arena form is NOT yet SSA: size-1 temp reads/writes are explicit OPX_GET /
OPX_SET instructions over interned places (SSA construction in M2 collapses
size-1 temps into def/use value edges; arrays and real-block accesses stay as
pinned OPX_GET/OPX_SET). "An instruction is its value": a runtime pure/impure
op instruction's value-id is used directly as an operand elsewhere.

Statement structure is recovered on export from FLAG_STMT_ROOT: exactly the
OPX_SET stores and the bare side-effecting op instructions are roots; every
other instruction is a sub-value reached as an operand. Operands always
reference strictly-earlier instructions in the same block (def-before-use in
the linear stream), which is what makes the tree rebuild a single forward pass.

------------------------------------------------------------------------------
BlockInfo
------------------------------------------------------------------------------
* instr_start/instr_count   contiguous slice of ``instrs`` owned by the block.
* test_val                  value-id of the block ``test`` (or -1).
* edge_start/edge_count     contiguous slice of ``edges`` (this block's OUTGOING
                            edges).
* phi_start/phi_count       contiguous slice of ``phis`` (block-head phis; empty
                            in M1 -- SSA is M2).
* rpo, idom                 reserved for M2 analyses (-1 == unset). Blocks are
                            numbered in reverse-postorder at marshal-in, so id
                            order already is a valid RPO for the initial CFG.

------------------------------------------------------------------------------
Edge                        per-edge; parallel edges between a block pair legal
------------------------------------------------------------------------------
* src, dst      block ids.
* cond_kind     EDGE_COND_NONE (default/"else"/unconditional) or
                EDGE_COND_VALUE (a specific case value).
* cond_is_int   display bit: reproduce the case as a Python ``int`` when set,
                else as ``float`` (raw CFGs use int cases, post-opt CFGs use
                float cases -- preserved for byte-faithful export).
* cond          f64 case value (meaningful only when cond_kind==VALUE). SCCP
                (M2) compares against constants in f64, which unifies int/float.

Legal per-block outgoing shapes (validated by ``verify``): {} exit, {NONE}
unconditional, {VALUE 0, NONE} two-way, {VALUE..., NONE} multi-way with
default, {VALUE...} default-less multi-way (missing default == jump to exit).

------------------------------------------------------------------------------
PlaceInfo                   interned
------------------------------------------------------------------------------
* kind          PLACE_TEMP_SCALAR (size 1), PLACE_TEMP_ARRAY (size>1),
                PLACE_TEMP_SIZE0 (size 0 placeholder), PLACE_REAL_BLOCK
                (static numbered memory block), PLACE_DYNAMIC_BLOCK (pointer
                deref: block computed at runtime).
* flags         PLACE_WRITABLE (this callback may write this location; reads of
                non-writable locations are motion/GVN-eligible in M2) and
                PLACE_BLOCK_IS_ENUM (real block reproduced as its Mode BlockData
                member on export, vs a raw int).
* block_ref     temp id (temps) | resolved block id (real) | block value-id
                (dynamic; a computed pointer value).
* index_val     value-id of a dynamic index, or -1 when the index is a compile
                -time constant folded into ``offset``.
* offset        constant offset (address == (index_val>=0 ? value(index_val):0)
                + offset).

Writability, resolved once at marshal-in (subsumes NormalizeBlocks): a resolved
BlockData is writable iff ``callback in block.writable`` (callback==None -> all
resolved blocks read-only); an unresolved raw-int block (mode==None) is
conservatively writable; a dynamic pointer target is conservatively writable.

------------------------------------------------------------------------------
consts                      f64 pool, interned by bit-pattern
------------------------------------------------------------------------------
Interning unifies int/float (2 and 2.0 share an id -- both are f64 2.0) but is
bit-pattern keyed, so -0.0 is a DISTINCT const from 0.0 and NaN is canonicalized
to a single quiet-NaN pattern. (The frontend's IRConst collapses -0.0 to 0, so
-0.0 only ever enters via the C fold kernels in a later wave; the table is ready
for it regardless.) A per-instruction FLAG_CONST_IS_INT records only the display
form for faithful export.

------------------------------------------------------------------------------
temps / names
------------------------------------------------------------------------------
* TempInfo.name_id  index into ``names`` (the source display name); size the
                    temp width. Temps are interned by (name, size).
* names             Python str list; boundary-only. Export renumbers temps
                    uniformly to v0/v1/... by first-touch order, so these source
                    names matter only for interning identity, not output.

Ownership: every buffer is owned by ``Func`` and freed once in ``__dealloc__``.
Growth doubles capacity via realloc. No per-node allocation.
"""

from libc.stdint cimport int16_t, int32_t, uint8_t, uint16_t, uint32_t


cdef struct Instr:
    uint16_t op
    uint8_t flags
    int32_t block
    int32_t arg_start
    int16_t nargs
    int32_t aux


cdef struct Edge:
    int32_t src
    int32_t dst
    uint8_t cond_kind
    uint8_t cond_is_int
    double cond


cdef struct BlockInfo:
    int32_t instr_start
    int32_t instr_count
    int32_t test_val
    int32_t edge_start
    int32_t edge_count
    int32_t phi_start
    int32_t phi_count
    int32_t rpo
    int32_t idom


cdef struct PlaceInfo:
    uint8_t kind
    uint8_t flags
    int32_t block_ref
    int32_t index_val
    int32_t offset


cdef struct TempInfo:
    int32_t name_id
    int32_t size


cdef struct PhiInfo:
    int32_t dest_temp
    int32_t operand_start
    int32_t operand_count


cdef enum:
    FLAG_PURE = 1
    FLAG_SIDE_EFFECT = 2
    FLAG_PINNED = 4
    FLAG_DEAD = 8
    FLAG_STMT_ROOT = 16
    FLAG_CONST_IS_INT = 32


cdef enum:
    PLACE_TEMP_SCALAR = 0
    PLACE_TEMP_ARRAY = 1
    PLACE_TEMP_SIZE0 = 2
    PLACE_REAL_BLOCK = 3
    PLACE_DYNAMIC_BLOCK = 4


cdef enum:
    PLACE_WRITABLE = 1
    PLACE_BLOCK_IS_ENUM = 2


cdef enum:
    EDGE_COND_NONE = 0
    EDGE_COND_VALUE = 1


cdef class Func:
    cdef:
        Instr* instrs
        int32_t n_instrs
        int32_t cap_instrs

        uint32_t* args
        int32_t n_args
        int32_t cap_args

        BlockInfo* blocks
        int32_t n_blocks
        int32_t cap_blocks

        Edge* edges
        int32_t n_edges
        int32_t cap_edges

        double* consts
        int32_t n_consts
        int32_t cap_consts

        PlaceInfo* places
        int32_t n_places
        int32_t cap_places

        TempInfo* temps
        int32_t n_temps
        int32_t cap_temps

        int32_t* phi_operands
        int32_t n_phi_operands
        int32_t cap_phi_operands

        PhiInfo* phis
        int32_t n_phis
        int32_t cap_phis

        int32_t entry_block

        # Boundary-only Python state (GIL held; never touched in nogil passes).
        list names
        dict _const_intern
        dict _temp_intern
        dict _place_intern
        object blocks_type
        object callback
        dict _block_enum_by_id
        dict _block_map

    # Growable-buffer + interning + marshal helpers (implemented in ir.pyx).
    cdef int32_t _alloc_instr(self) except -1
    cdef int32_t _emit(self, uint16_t op, uint8_t flags, int32_t block, int32_t aux, list arg_vids) except -1
    cdef int32_t _intern_const(self, double d) except -1
    cdef int32_t _intern_temp(self, object temp) except -1
    cdef bint _writable_for_block(self, object member) except -1
    cdef int32_t _intern_place(self, object place, int32_t block_id) except -1
    cdef int32_t _value_of(self, object node, int32_t block_id) except -1
    cdef int32_t _emit_const(self, object value, int32_t block_id) except -1
    cdef int32_t _emit_pure(self, object node, int32_t block_id) except -1
    cdef int32_t _emit_impure(self, object node, int32_t block_id) except -1
    cdef int _emit_stmt(self, object stmt, int32_t block_id) except -1
    cdef int _push_edge(self, int32_t src, int32_t dst, object cond) except -1
    cdef int _marshal(self, object entry, object mode, object callback) except -1
    cdef object _make_const(self, double d, bint is_int)
    cdef object _export_place(self, int32_t pid, dict names)
    cdef object _export_value(self, int32_t vid, dict names, bint as_read_place)
    cdef object _export_stmt(self, int32_t i, dict names)
    cdef _assign_temp_names(self, dict names)
    cdef object _export_phis(self, int32_t bid, dict names, list py_blocks, list incoming)
