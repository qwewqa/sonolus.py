# cython: language_level=3
"""Liveness, dominators, and loop forest over the arena IR.

Backward dataflow over per-block bitsets indexed by temp id, at *statement*
granularity, plus the forward array-init pass that decides when an
array-element write kills whole-array liveness. Non-SSA form; feeds the
allocators in ``lower.pyx``.

See the module docstring in ``analysis.pyx`` for the full contract.
"""

from libc.stdint cimport int32_t, uint8_t, uint64_t

from sonolus.backend._opt.ir cimport Func


cdef inline bint bs_get(const uint64_t* bs, int32_t i) noexcept nogil:
    return <bint>((bs[i >> 6] >> (i & 63)) & <uint64_t>1)


cdef inline void bs_set(uint64_t* bs, int32_t i) noexcept nogil:
    bs[i >> 6] |= (<uint64_t>1 << (i & 63))


cdef inline void bs_clear(uint64_t* bs, int32_t i) noexcept nogil:
    bs[i >> 6] &= ~(<uint64_t>1 << (i & 63))


cdef class Liveness:
    # Owning arena (kept alive; only its C buffers are read in nogil regions).
    cdef Func func

    cdef int32_t n_temps
    cdef int32_t n_words     # 64-bit words per per-block bitset
    cdef int32_t n_blocks
    cdef int32_t n_instrs
    cdef int32_t n_roots     # count of FLAG_STMT_ROOT instructions

    # Per-block bitsets over temp ids (n_blocks * n_words each).
    cdef uint64_t* live_in
    cdef uint64_t* live_out
    cdef uint64_t* array_defs_out   # arrays provably defined on all paths at block end
    cdef uint64_t* array_mask       # bitset (n_words) of size>1 temp ids

    # Per-statement-root live-out (n_roots * n_words); root_slot maps an instr
    # index to its root ordinal (or -1 for non-root instrs).
    cdef uint64_t* root_live
    cdef int32_t* root_slot

    # Per-instr flag: this OPX_SET is the first (initializing) write to its array.
    cdef uint8_t* is_array_init

    # Live-out bitset for a statement root by instr index (NULL args guarded by caller).
    cdef uint64_t* _root_live_ptr(self, int32_t instr_idx) noexcept nogil
    cdef object _bitset_names(self, const uint64_t* bs)


cdef Liveness compute_liveness(Func func)


# --------------------------------------------------------------------------
# Dominators (Cooper-Harvey-Kennedy iterative idom over RPO).
#
# Block ids are already reverse-postorder (marshal / cfg_cleanup number them so),
# so id order == RPO and the idom intersection compares ids directly. Results:
#
# * ``idom[b]``          immediate dominator (idom[entry] == entry); also written
#                        back into ``func.blocks[b].idom`` (the reserved field).
# * dom-tree children    CSR (``child_head`` / ``child_list``).
# * ``tin`` / ``tout``   Euler-tour in/out numbers over the dom tree, giving an
#                        O(1) ``dominates(a, b)`` query (a dom b iff a's interval
#                        contains b's).
# * predecessor CSR      (``pred_head`` / ``pred_src``) built once and kept.
# --------------------------------------------------------------------------

cdef class Dominators:
    cdef Func func
    cdef int32_t n_blocks
    cdef int32_t* idom          # [nb]      immediate dominator (idom[entry]==entry)
    cdef int32_t* tin           # [nb]      Euler-tour enter time over the dom tree
    cdef int32_t* tout          # [nb]      Euler-tour exit time
    cdef int32_t* pred_head     # [nb+1]    predecessor CSR offsets
    cdef int32_t* pred_src      # [ne]      predecessor block ids
    cdef int32_t* child_head    # [nb+1]    dom-tree child CSR offsets
    cdef int32_t* child_list    # [nb]      dom-tree children (each non-entry once)

    # a dominates b (reflexive: a dom a). O(1) via the Euler intervals.
    cdef bint dominates(self, int32_t a, int32_t b) noexcept nogil


cdef Dominators compute_dominators(Func func)


# --------------------------------------------------------------------------
# Loop forest (natural loops from dominator back edges).
#
# A back edge is an edge ``u -> h`` whose head ``h`` dominates its tail ``u``;
# ``h`` is a loop header. The natural loop of a header is the union, over all its
# latches ``u``, of the blocks that reach ``u`` without passing through ``h``
# (standard backward reachability). One loop per distinct header (parallel/multiple
# latches merge). Loops nest by header containment. Used by LICM (hoist targets,
# preheaders):
#
# * ``depth[b]``       number of loops containing block ``b`` (0 == not in a loop).
# * ``innermost[b]``   deepest loop id containing ``b`` (or -1).
# * ``header[L]``      the header block of loop ``L``.
# * ``parent[L]``      enclosing loop id (or -1), i.e. the loop tree.
# * ``in_loop(L, b)``  O(1) membership (block bitset).
# * ``crosses_loop(def_b, use_b)`` -- true iff sinking a value defined in
#   ``def_b`` to a use in ``use_b`` would move it into a strictly deeper loop
#   (``use_b`` is in a loop that does not contain ``def_b``); the treeify
#   cross-block fold gate.
# --------------------------------------------------------------------------

cdef class LoopForest:
    cdef Func func
    cdef int32_t n_blocks
    cdef int32_t n_loops
    cdef int32_t nwb                # 64-bit words per block-bitset
    cdef int32_t* depth             # [nb]        loop-nesting depth of each block
    cdef int32_t* innermost         # [nb]        innermost loop id, or -1
    cdef int32_t* header            # [n_loops]   header block of each loop
    cdef int32_t* parent            # [n_loops]   enclosing loop id, or -1
    cdef int32_t* loop_depth        # [n_loops]   nesting depth of each loop (>=1)
    cdef uint64_t* body             # [n_loops*nwb] membership bitset over block ids

    cdef bint in_loop(self, int32_t loop_id, int32_t block) noexcept nogil
    cdef bint crosses_loop(self, int32_t def_block, int32_t use_block) noexcept nogil


cdef LoopForest compute_loops(Func func, Dominators D)
