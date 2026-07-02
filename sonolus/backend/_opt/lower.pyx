# cython: language_level=3
"""Temp-memory allocation and lowering over the arena IR (milestone M1, §7.5).

Rewrites every temp place (size-1 scalar, size>1 array, size-0 placeholder) to a
``BlockPlace(10000, ...)`` real-block place, with three strategies:

* ``bump``     -- no liveness; sequential assignment in temp-id order.
* ``packing``  -- interference from statement-granularity live sets, then TRUE
                  first-fit gap packing over slot intervals sorted by
                  ``(-size, temp id)``; arrays occupy a contiguous ``[base,
                  base+size)`` range. Plus dead-store elimination.
* ``try_bump`` -- bump if it fits the 4096-slot cap, else ``packing``.

Dead-store elimination mirrors the old ``Allocate.update_stmt`` exactly: a store
is dead iff its temp target is not live-out, or it is a self-copy; a dead store
with a side-effecting value is replaced by the bare value statement, others are
dropped. Determinism comes from temp-id order (marshal first-touch) and the
``(-size, temp id)`` sort key -- no Python strings involved.

The 4096-slot cap raises ``ValueError("Temporary memory limit exceeded")``, the
same message as the old ``allocate.py``.
"""

from libc.stdint cimport int32_t, uint8_t, uint32_t, uint64_t
from libc.stdlib cimport calloc, free, malloc

from sonolus.backend._opt.ir cimport (
    FLAG_SIDE_EFFECT,
    FLAG_STMT_ROOT,
    Func,
    Instr,
    PLACE_REAL_BLOCK,
    PLACE_TEMP_ARRAY,
    PLACE_TEMP_SCALAR,
    PLACE_TEMP_SIZE0,
    PlaceInfo,
)
from sonolus.backend._opt._ops_gen cimport OPX_GET, OPX_SET
from sonolus.backend._opt.analysis cimport Liveness, bs_get, bs_set, compute_liveness

from sonolus.backend._opt.ir import marshal_in, to_basic_blocks


# --------------------------------------------------------------------------
# Bump allocator (no liveness, no dead-store elimination).
# --------------------------------------------------------------------------

cdef void _bump(Func func, int32_t* temp_offset) except *:
    cdef int32_t index = 0
    cdef int32_t t, size
    for t in range(func.n_temps):
        size = func.temps[t].size
        if size == 0:
            continue
        temp_offset[t] = index
        index += size
        if index >= TEMP_SIZE:
            raise ValueError("Temporary memory limit exceeded")


cdef bint _bump_fits(Func func):
    cdef int32_t total = 0
    cdef int32_t t, size
    for t in range(func.n_temps):
        size = func.temps[t].size
        if size > 0:
            total += size
    return total < TEMP_SIZE


# --------------------------------------------------------------------------
# Interference-based first-fit packing.
# --------------------------------------------------------------------------

cdef void _pack(Func func, Liveness L, int32_t* temp_offset) except *:
    cdef int32_t n_temps = func.n_temps
    cdef int32_t nw = L.n_words
    cdef int32_t ni = func.n_instrs
    cdef Instr* instrs = func.instrs
    cdef uint32_t* args = func.args
    cdef int32_t i, t, u, w, rs, size, offset, new_off, us, ue
    cdef uint64_t* rl
    cdef bint changed

    if n_temps == 0:
        return

    # nonzero mask: temps with size > 0 (size-0 temps never interfere).
    cdef uint64_t* nonzero = <uint64_t*>calloc(<size_t>(nw if nw > 0 else 1), sizeof(uint64_t))
    cdef uint64_t* adj = <uint64_t*>calloc(<size_t>(n_temps * nw if nw > 0 else 1), sizeof(uint64_t))
    cdef uint64_t* work = <uint64_t*>calloc(<size_t>(nw if nw > 0 else 1), sizeof(uint64_t))
    if nonzero == NULL or adj == NULL or work == NULL:
        free(nonzero); free(adj); free(work)
        raise MemoryError()
    try:
        for t in range(n_temps):
            if func.temps[t].size > 0:
                bs_set(nonzero, t)

        # Build interference: for each OPX_SET statement, all size>0 temps in its
        # live-out set mutually interfere (old get_interference semantics).
        for i in range(ni):
            if instrs[i].op != OPX_SET:
                continue
            rs = L.root_slot[i]
            if rs < 0:
                continue
            rl = &L.root_live[rs * nw]
            for w in range(nw):
                work[w] = rl[w] & nonzero[w]
            for t in range(n_temps):
                if bs_get(work, t):
                    for w in range(nw):
                        adj[t * nw + w] |= work[w]

        # Order: (-size, temp id). Stable, string-free.
        order = sorted(
            (t for t in range(n_temps) if func.temps[t].size > 0),
            key=lambda x: (-func.temps[x].size, x),
        )

        for t in order:
            size = func.temps[t].size
            offset = 0
            changed = True
            while changed:
                changed = False
                new_off = offset
                for u in range(n_temps):
                    if u == t:
                        continue
                    if not bs_get(&adj[t * nw], u):
                        continue
                    if temp_offset[u] < 0:
                        continue
                    us = temp_offset[u]
                    ue = us + func.temps[u].size
                    # overlap of [offset, offset+size) with [us, ue)
                    if us < offset + size and ue > offset:
                        if ue > new_off:
                            new_off = ue
                if new_off != offset:
                    offset = new_off
                    changed = True
            if offset + size > TEMP_SIZE:
                raise ValueError("Temporary memory limit exceeded")
            temp_offset[t] = offset
    finally:
        free(nonzero)
        free(adj)
        free(work)


# --------------------------------------------------------------------------
# Dead-store elimination (mirror of old Allocate.update_stmt).
# --------------------------------------------------------------------------

cdef void _dead_store_elim(Func func, Liveness L):
    cdef int32_t ni = func.n_instrs
    cdef int32_t nw = L.n_words
    cdef Instr* instrs = func.instrs
    cdef uint32_t* args = func.args
    cdef PlaceInfo* places = func.places
    cdef int32_t i, rs, pid, vid, t
    cdef uint8_t kind
    cdef uint64_t* rl
    cdef bint disj1, disj2, is_live

    for i in range(ni):
        if instrs[i].op != OPX_SET:
            continue
        if not (instrs[i].flags & FLAG_STMT_ROOT):
            continue
        rs = L.root_slot[i]
        rl = &L.root_live[rs * nw]
        pid = instrs[i].aux
        vid = <int32_t>args[instrs[i].arg_start]
        kind = places[pid].kind
        disj1 = False
        if kind == PLACE_TEMP_SCALAR or kind == PLACE_TEMP_ARRAY or kind == PLACE_TEMP_SIZE0:
            t = places[pid].block_ref
            if not bs_get(rl, t):
                disj1 = True
        disj2 = (instrs[vid].op == OPX_GET and instrs[vid].aux == pid)
        is_live = not (disj1 or disj2)
        if is_live:
            continue
        # Dead store.
        instrs[i].flags &= <uint8_t>(~FLAG_STMT_ROOT)
        if instrs[vid].flags & FLAG_SIDE_EFFECT:
            # Keep the side effect: promote the value to a bare statement root.
            instrs[vid].flags |= FLAG_STMT_ROOT


# --------------------------------------------------------------------------
# Place rewriting: temp -> real block 10000.
# --------------------------------------------------------------------------

cdef void _rewrite_places(Func func, int32_t* temp_offset):
    cdef PlaceInfo* places = func.places
    cdef int32_t pid, t
    cdef uint8_t kind
    for pid in range(func.n_places):
        kind = places[pid].kind
        if kind == PLACE_TEMP_SIZE0:
            places[pid].kind = PLACE_REAL_BLOCK
            places[pid].flags = 0
            places[pid].block_ref = TEMP_BLOCK
            places[pid].offset = -1
        elif kind == PLACE_TEMP_SCALAR or kind == PLACE_TEMP_ARRAY:
            t = places[pid].block_ref
            places[pid].offset = temp_offset[t] + places[pid].offset
            places[pid].kind = PLACE_REAL_BLOCK
            places[pid].flags = 0
            places[pid].block_ref = TEMP_BLOCK


# --------------------------------------------------------------------------
# Driver.
# --------------------------------------------------------------------------

cdef void allocate_func(Func func, int32_t strategy) except *:
    cdef int32_t n_temps = func.n_temps
    cdef int32_t* temp_offset = <int32_t*>malloc(<size_t>(n_temps if n_temps > 0 else 1) * sizeof(int32_t))
    if temp_offset == NULL:
        raise MemoryError()
    cdef int32_t t
    for t in range(n_temps):
        temp_offset[t] = -2  # unallocated sentinel
    cdef Liveness L
    try:
        if strategy == ALLOC_BUMP:
            _bump(func, temp_offset)
        elif strategy == ALLOC_PACKING:
            L = compute_liveness(func)
            _pack(func, L, temp_offset)
            _dead_store_elim(func, L)
        elif strategy == ALLOC_TRY_BUMP:
            if _bump_fits(func):
                _bump(func, temp_offset)
            else:
                L = compute_liveness(func)
                _pack(func, L, temp_offset)
                _dead_store_elim(func, L)
        else:
            raise ValueError(f"Unknown allocation strategy code {strategy}")
        _rewrite_places(func, temp_offset)
    finally:
        free(temp_offset)


cdef int32_t _strategy_code(object strategy) except -1:
    if strategy == "bump":
        return ALLOC_BUMP
    if strategy == "packing":
        return ALLOC_PACKING
    if strategy == "try_bump":
        return ALLOC_TRY_BUMP
    raise ValueError(f"Unknown allocation strategy {strategy!r} (expected 'bump', 'packing', or 'try_bump')")


# --------------------------------------------------------------------------
# Python-visible API.
# --------------------------------------------------------------------------

def allocate_arena(entry, mode=None, callback=None, strategy="packing"):
    """Marshal ``entry`` and allocate temps, returning the mutated arena ``Func``.

    Exposed for tests/inspection (``func.verify()``, ``func.stats()``). Most
    callers want :func:`run_allocate` which exports back to a CFG.
    """
    cdef int32_t code = _strategy_code(strategy)
    cdef Func func = <Func>marshal_in(entry, mode, callback)
    allocate_func(func, code)
    return func


def run_allocate(entry, mode=None, callback=None, strategy="packing"):
    """Marshal, allocate temps, and export back to a Python ``BasicBlock`` CFG.

    ``strategy`` is one of ``"bump"``, ``"packing"`` (default), ``"try_bump"``.
    """
    return to_basic_blocks(allocate_arena(entry, mode, callback, strategy))
