# cython: language_level=3
"""Liveness analysis over the arena IR (milestone M1, §7.5).

Backward dataflow over per-block bitsets indexed by temp id, at *statement*
granularity, plus the forward array-init pass that decides when an
array-element write kills whole-array liveness. This mirrors the semantics of
the old ``sonolus/backend/optimize/liveness.py`` pass exactly (non-SSA form),
and feeds the allocators in ``lower.pyx``.

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
# Dominators (Cooper-Harvey-Kennedy iterative idom over RPO) -- milestone M2.
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
