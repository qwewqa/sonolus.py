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
