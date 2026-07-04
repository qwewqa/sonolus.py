# cython: language_level=3
"""Arena IR for the Cython optimizer core.

Declares the flat, arena-allocated IR and the C layout the ``nogil`` passes read
directly. This ``.pxd`` is the load-bearing contract the mid-end (SSA/SCCP/GVN),
lowering/allocation, and emission build on.

Design summary (read before extending):

One ``Func`` arena per callback. No global mutable state (op metadata is the
static ``const`` table in ``_ops_gen``), so the passes are thread-safe by
construction -- builds are currently serial, and the ``nogil`` annotations are
kept only to document that GIL-independence, not to spin up threads. All hot
state is flat C arrays of integer ids; the only Python objects (``names``, the
temp/place interning dicts, and the khash const-intern map) are touched at the
marshal boundaries under the GIL and never inside ``nogil`` passes.

Everything is index-addressed:

* a *value* is an instruction; its value-id is its index into ``instrs``.
* a *block* is its index into ``blocks``.
* a *place*/``const``/``temp``/``edge`` is its index into the matching array.

------------------------------------------------------------------------------
Instr (AoS)               one array; per-block slice == the initial schedule
------------------------------------------------------------------------------
* op        u16   opcode: a runtime ``Op`` id (0..OP_RUNTIME_COUNT-1, ==
                  ops.py declaration order) or a synthetic ``OPX_*`` id.
* flags     u8    FLAG_* bitset (pure/side_effect/pinned/stmt_root/
                  const_is_int).
* block     i32   owning block id.
* arg_start i32   start index into ``args`` (operand value-ids).
* nargs     i16   operand count.
* aux       i32   op-specific: OPX_CONST -> const id; OPX_GET/OPX_SET ->
                  place id; runtime ops -> unused (-1).

The non-SSA arena form (marshal-in output, cfg_cleanup output, out-of-SSA
output) is NOT SSA: size-1 temp reads/writes are explicit OPX_GET / OPX_SET
instructions over interned places. "An instruction is its value": a runtime
pure/impure op instruction's value-id is used directly as an operand elsewhere.

Statement structure is recovered on export from FLAG_STMT_ROOT: exactly the
OPX_SET stores and the bare side-effecting op instructions are roots; every
other instruction is a sub-value reached as an operand. In non-SSA form operands
always reference strictly-earlier instructions in the same block (def-before-use
in the linear stream), which is what makes the tree rebuild a single forward pass.

SSA form (``is_ssa`` set; produced by ``build_ssa`` in midend.pyx, consumed by
``out_of_ssa``): size-1 temp OPX_GET/OPX_SET dissolve into value edges. Phis are
real ``OPX_PHI`` instructions at each block head (the leading ``phi_count``
instructions of the block slice, starting at ``phi_start``); a phi's ``aux`` is
the source temp id it was created for (naming only) and its ``arg`` slice holds
exactly one operand value-id PER INCOMING EDGE, in the incoming-edge order
defined below. ``OPX_UNDEF`` is a single value (id ``undef_val``, first instr of
the entry block) standing for a read of a never-written scalar. UNDEF is a normal
distinct phi operand during trivial-phi elimination -- ``phi(UNDEF, v)`` is NOT
folded to ``v`` (folding a live uninitialized merge mis-compiles and can create
def-before-use cycles; provably-dead collapse belongs to SCCP edge executability).
A surviving live UNDEF lowers to one shared never-written temp in out-of-SSA. In
SSA form a non-phi operand def
*dominates* its use (so an operand may reference an instruction in a dominating
block, not just the same block; still def-before-use in the flat stream since ids
are RPO), while a phi operand's def dominates the corresponding predecessor's exit
(and may reference a LATER instruction across a loop back edge). ``verify()``
checks these when ``is_ssa`` is set.

Incoming-edge order contract (the keystone of phi operand <-> edge mapping): the
incoming edges of a block are enumerated in ASCENDING global edge-index order
(edge index into ``edges``; edges are grouped by src block in block/RPO order).
Phi operand ``k`` corresponds to the ``k``-th incoming edge in that order. Both
``build_ssa`` (operand construction) and ``_export_phis`` / ``out_of_ssa`` use
exactly this order. Parallel edges from one predecessor each carry their own
operand, but the value at a predecessor's exit is unique, so parallel same-pred
operands are EQUAL (verified; the per-pred ``BasicBlock.phis`` export normalizes
them, checking equality).

------------------------------------------------------------------------------
BlockInfo
------------------------------------------------------------------------------
* instr_start/instr_count   contiguous slice of ``instrs`` owned by the block.
* test_val                  value-id of the block ``test`` (or -1).
* edge_start/edge_count     contiguous slice of ``edges`` (this block's OUTGOING
                            edges).
* phi_start/phi_count       the block's leading ``OPX_PHI`` instructions (SSA form
                            only; both 0 in non-SSA form). phi_start is the index
                            of the first phi (== instr_start, except the entry
                            block whose OPX_UNDEF precedes its phis).
* rpo, idom                 rpo == block id (blocks are numbered in reverse-
                            postorder at marshal-in / cfg_cleanup). idom is filled
                            by ``compute_dominators`` (analysis.pyx); -1 == unset.

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
                compares against constants in f64, which unifies int/float.

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
                non-writable locations are motion/GVN-eligible) and
                PLACE_BLOCK_IS_ENUM (real block reproduced as its Mode BlockData
                member on export, vs a raw int).
* block_ref     temp id (temps) | resolved block id (real) | block value-id
                (dynamic; a computed pointer value).
* index_val     value-id of a dynamic index, or -1 when the index is a compile
                -time constant folded into ``offset``.
* offset        constant offset (address == (index_val>=0 ? value(index_val):0)
                + offset).

Writability, resolved once at marshal-in: a resolved
BlockData is writable iff ``callback in block.writable`` (callback==None -> all
resolved blocks read-only); an unresolved raw-int block (mode==None) is
conservatively writable; a dynamic pointer target is conservatively writable.

------------------------------------------------------------------------------
consts                      f64 pool, interned by bit-pattern
------------------------------------------------------------------------------
Interning unifies int/float (2 and 2.0 share an id -- both are f64 2.0) but is
bit-pattern keyed, so -0.0 is a DISTINCT const from 0.0 and NaN is canonicalized
to a single quiet-NaN pattern. (The frontend's IRConst collapses -0.0 to 0, so
-0.0 only ever enters via the C fold kernels.) A per-instruction
FLAG_CONST_IS_INT records only the display form for faithful export.

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

from sonolus.backend._opt._khash cimport kh_i64i32_t


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


cdef enum:
    FLAG_PURE = 1
    FLAG_SIDE_EFFECT = 2
    FLAG_PINNED = 4
    FLAG_STMT_ROOT = 8
    FLAG_CONST_IS_INT = 16
    # Set by if-conversion (lower.pyx) on every strictly
    # pure arm value it hoists into the head block P as an operand subtree of an
    # ``If``/``Switch`` select. It is a MUST-FOLD contract for treeify (lower.pyx): the
    # value is single-use by construction and MUST fold into its consuming select
    # tree -- never materialized to a temp, never duplicated into several temps.
    # Materialising an arm value would evaluate it UNCONDITIONALLY on both control
    # paths (e.g. a guarded ``a/b``), which the interpret.py oracle faults on
    # (ZeroDivisionError) even though the real runtime's lazy ``If`` tolerates it --
    # i.e. a miscompile. ``_Lower._analyze`` reads this bit and forces FOLD,
    # overriding any cost/loop rule, and asserts single-use. u8 ``flags`` bit 5;
    # values 64 and 128 remain free.
    FLAG_MUST_FOLD = 32


cdef enum:
    PLACE_TEMP_SCALAR = 0
    PLACE_TEMP_ARRAY = 1
    PLACE_TEMP_SIZE0 = 2
    PLACE_REAL_BLOCK = 3
    PLACE_DYNAMIC_BLOCK = 4


cdef enum:
    PLACE_WRITABLE = 1
    PLACE_BLOCK_IS_ENUM = 2
    # A constant-index read of a non-writable block whose resolved name is in
    # ``RUNTIME_CONSTANT_BLOCKS`` (set once at marshal-in, ir.pyx). The real
    # runtime constant-folds such reads to a single push, so the treeify cost
    # model (lower.pyx) treats a pure tree over these + OPX_CONSTs as effective
    # cost 1 and duplicates it regardless of size rather than materializing a
    # temp (which would defeat the fold).
    PLACE_RUNTIME_CONST = 4


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

        int32_t entry_block

        # SSA form: set by build_ssa, cleared by out_of_ssa / marshal_in.
        # undef_val is the value-id of the shared OPX_UNDEF instr (or -1).
        bint is_ssa
        int32_t undef_val
        # Carrier for value ids whose non-dominating uses verify() should tolerate
        # (a dead-path relaxation). Retained as the verify() relaxation hook; UNDEF
        # is a normal distinct phi operand (trivial-phi elimination does not widen
        # it), so this stays empty in practice. None outside SSA form. (Python set,
        # boundary-only.)
        object _ssa_undef

        # Const-pool interning: f64 bit pattern -> const id. A vendored khash int
        # map (not a Python dict) so interning a const is a raw uint64 lookup with
        # no boxing. Owned by ``Func`` (kh_init in __cinit__, kh_destroy in
        # __dealloc__). GIL held at every touch; never mutated in nogil passes.
        kh_i64i32_t* _const_intern

        # Boundary-only Python state (GIL held; never touched in nogil passes).
        list names
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
    cdef int _rebuild_const_intern(self) except -1
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
    cdef tuple _export_place_components(self, int32_t pid, dict names)
    cdef object _export_value(self, int32_t vid, dict names, bint as_read_place)
    cdef object _export_stmt(self, int32_t i, dict names)
    cdef object _export_fused_rmw(self, int32_t i, uint16_t op, dict names)
    cdef _assign_temp_names(self, dict names)
    # SSA export helpers (_export_ssa / _export_phis / _ssa_* / _dom) are plain
    # ``def`` methods in ir.pyx -- they run under the GIL at the export boundary.
