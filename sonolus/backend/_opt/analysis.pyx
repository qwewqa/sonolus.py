# cython: language_level=3
"""Liveness analysis over the arena IR.

Liveness over the non-SSA arena form (size-1 temp reads/writes are explicit
OPX_GET / OPX_SET; there are no phis). It computes, for every basic block:

* ``live_in`` / ``live_out`` bitsets over temp ids;
* per-statement-root live-out bitsets (``root_live``), shared with the
  allocators for interference and dead-store elimination;
* ``is_array_init`` per OPX_SET (the forward array pass), and
  ``array_defs_out`` per block.

Semantics:

* Uses of a statement = every temp *read* anywhere in the operand tree
  (OPX_GET of a temp place, plus temps read in a dynamic block/index subtree).
  The OPX_SET *target* temp is a def, not a use.
* Def = the OPX_SET target temp when it is a size-1 (scalar) temp.
* array_defs = the OPX_SET target when it is a size>1 (array) temp.
* Array rule: an array is live once any element is *read*; an array-element
  *write* kills whole-array liveness only when it is provably the first write on
  every path (``is_array_init``); arrays are not live before any write (the
  live-out filter drops arrays not yet in ``array_defs_out``).
* Size-0 temps are treated as ordinary temps for use-gen (a read makes them
  live) but are never defs/array_defs; they are filtered out at allocation time.
* ``can_skip``: a store whose value is not side-effecting and whose def set is
  non-empty and disjoint from live-out contributes nothing (its uses do not
  keep operands alive).
* Block tests count as uses at block end.

Backward dataflow runs to a fixpoint over a worklist seeded from exit blocks; the
forward array-init pass runs to a fixpoint from the entry block. Both are monotone
so the result is order-independent (deterministic).
"""

from libc.stdint cimport int32_t, int64_t, uint8_t, uint16_t, uint32_t, uint64_t
from libc.stdlib cimport calloc, free, malloc
from libc.string cimport memcmp, memcpy, memset

from sonolus.backend._opt.ir cimport (
    BlockInfo,
    Edge,
    FLAG_SIDE_EFFECT,
    FLAG_STMT_ROOT,
    Func,
    Instr,
    PLACE_DYNAMIC_BLOCK,
    PLACE_TEMP_ARRAY,
    PLACE_TEMP_SCALAR,
    PLACE_TEMP_SIZE0,
    PlaceInfo,
)
from sonolus.backend._opt._ops_gen cimport OPX_CONST, OPX_GET, OPX_SET

from sonolus.backend._opt.ir import marshal_in


# --------------------------------------------------------------------------
# Use-gen: collect the temps read in a value / place subtree into ``bs``.
# All pointer-only, so callable from nogil regions.
# --------------------------------------------------------------------------

cdef void _gen_value(Instr* instrs, uint32_t* args, PlaceInfo* places,
                     uint64_t* bs, int32_t vid) noexcept nogil:
    cdef uint16_t op = instrs[vid].op
    cdef int32_t astart, nargs, k, pid
    cdef uint8_t kind
    cdef int32_t br, iv
    if op == OPX_CONST:
        return
    if op == OPX_GET:
        pid = instrs[vid].aux
        kind = places[pid].kind
        br = places[pid].block_ref
        iv = places[pid].index_val
        if kind == PLACE_TEMP_SCALAR or kind == PLACE_TEMP_ARRAY or kind == PLACE_TEMP_SIZE0:
            bs_set(bs, br)
        elif kind == PLACE_DYNAMIC_BLOCK:
            _gen_value(instrs, args, places, bs, br)
        if iv >= 0:
            _gen_value(instrs, args, places, bs, iv)
        return
    # Runtime op (pure or side-effecting): read every operand subtree.
    astart = instrs[vid].arg_start
    nargs = instrs[vid].nargs
    for k in range(nargs):
        _gen_value(instrs, args, places, bs, <int32_t>args[astart + k])


cdef void _gen_place_write(Instr* instrs, uint32_t* args, PlaceInfo* places,
                           uint64_t* bs, int32_t pid) noexcept nogil:
    # Uses contributed by an OPX_SET *target* place: the block temp is a def
    # (not a use), but a dynamic block pointer and a dynamic index are reads.
    cdef uint8_t kind = places[pid].kind
    cdef int32_t br = places[pid].block_ref
    cdef int32_t iv = places[pid].index_val
    if kind == PLACE_DYNAMIC_BLOCK:
        _gen_value(instrs, args, places, bs, br)
    if iv >= 0:
        _gen_value(instrs, args, places, bs, iv)


# --------------------------------------------------------------------------
# Bitset helpers over multi-word blocks.
# --------------------------------------------------------------------------

cdef inline uint64_t* _c64(int64_t count) except NULL:
    # count is int64 so callers can pass nb*nw products without int32 overflow;
    # an oversized request fails cleanly in calloc (MemoryError) rather than
    # wrapping to a small buffer that the strided passes then overrun.
    cdef int64_t n = count if count > 0 else 1
    cdef uint64_t* p = <uint64_t*>calloc(<size_t>n, sizeof(uint64_t))
    if p == NULL:
        raise MemoryError()
    return p


cdef inline bint _or_into(uint64_t* dst, const uint64_t* src, int32_t nw) noexcept nogil:
    # dst |= src; return whether dst changed.
    cdef int32_t w
    cdef uint64_t before
    cdef bint changed = False
    for w in range(nw):
        before = dst[w]
        dst[w] = before | src[w]
        if dst[w] != before:
            changed = True
    return changed


# --------------------------------------------------------------------------
# Liveness result object.
# --------------------------------------------------------------------------

cdef class Liveness:
    def __cinit__(self):
        self.func = None
        self.live_in = NULL
        self.live_out = NULL
        self.array_defs_out = NULL
        self.array_mask = NULL
        self.root_live = NULL
        self.root_slot = NULL
        self.is_array_init = NULL

    def __dealloc__(self):
        free(self.live_in)
        free(self.live_out)
        free(self.array_defs_out)
        free(self.array_mask)
        free(self.root_live)
        free(self.root_slot)
        free(self.is_array_init)

    cdef uint64_t* _root_live_ptr(self, int32_t instr_idx) noexcept nogil:
        cdef int32_t rs = self.root_slot[instr_idx]
        return &self.root_live[<int64_t>rs * self.n_words]

    cdef object _bitset_names(self, const uint64_t* bs):
        cdef Func f = self.func
        cdef set out = set()
        cdef int32_t t
        for t in range(self.n_temps):
            if bs_get(bs, t):
                out.add(f.names[f.temps[t].name_id])
        return out

    # --- python debug accessors -------------------------------------------

    def live_in_names(self):
        cdef dict d = {}
        cdef int32_t b
        for b in range(self.n_blocks):
            d[b] = self._bitset_names(&self.live_in[b * self.n_words])
        return d

    def live_out_names(self):
        cdef dict d = {}
        cdef int32_t b
        for b in range(self.n_blocks):
            d[b] = self._bitset_names(&self.live_out[b * self.n_words])
        return d

    def root_live_names(self):
        """Map each statement-root instr index to its live-out temp-name set."""
        cdef dict d = {}
        cdef Func f = self.func
        cdef int32_t i
        for i in range(self.n_instrs):
            if self.root_slot[i] >= 0:
                d[i] = self._bitset_names(&self.root_live[self.root_slot[i] * self.n_words])
        return d

    def array_defs_out_names(self):
        cdef dict d = {}
        cdef int32_t b
        for b in range(self.n_blocks):
            d[b] = self._bitset_names(&self.array_defs_out[b * self.n_words])
        return d

    def is_array_init_map(self):
        """Map each array-target OPX_SET root instr index to its is_array_init bit."""
        cdef dict d = {}
        cdef Func f = self.func
        cdef int32_t i, pid
        for i in range(self.n_instrs):
            if self.root_slot[i] < 0:
                continue
            if f.instrs[i].op != OPX_SET:
                continue
            pid = f.instrs[i].aux
            if f.places[pid].kind == PLACE_TEMP_ARRAY:
                d[i] = bool(self.is_array_init[i])
        return d


# --------------------------------------------------------------------------
# The analysis.
# --------------------------------------------------------------------------

cdef Liveness compute_liveness(Func func):
    cdef Liveness L = Liveness()
    L.func = func

    cdef int32_t n_temps = func.n_temps
    cdef int32_t nb = func.n_blocks
    cdef int32_t ni = func.n_instrs
    cdef int32_t ne = func.n_edges
    cdef int32_t nw = (n_temps + 63) >> 6
    L.n_temps = n_temps
    L.n_blocks = nb
    L.n_instrs = ni
    L.n_words = nw

    cdef Instr* instrs = func.instrs
    cdef uint32_t* args = func.args
    cdef PlaceInfo* places = func.places
    cdef BlockInfo* blocks = func.blocks
    cdef Edge* edges = func.edges

    cdef int32_t i, b, t, e, w

    # Root slots.
    L.root_slot = <int32_t*>malloc(<size_t>(ni if ni > 0 else 1) * sizeof(int32_t))
    if L.root_slot == NULL:
        raise MemoryError()
    cdef int32_t n_roots = 0
    for i in range(ni):
        if instrs[i].flags & FLAG_STMT_ROOT:
            L.root_slot[i] = n_roots
            n_roots += 1
        else:
            L.root_slot[i] = -1
    L.n_roots = n_roots

    L.live_in = _c64(<int64_t>nb * nw)
    L.live_out = _c64(<int64_t>nb * nw)
    L.array_defs_out = _c64(<int64_t>nb * nw)
    L.array_mask = _c64(nw)
    L.root_live = _c64(<int64_t>n_roots * nw)
    L.is_array_init = <uint8_t*>calloc(<size_t>(ni if ni > 0 else 1), sizeof(uint8_t))
    if L.is_array_init == NULL:
        raise MemoryError()

    # array_mask: temps with size > 1.
    for t in range(n_temps):
        if func.temps[t].size > 1:
            bs_set(L.array_mask, t)

    # A CFG with no exit block at all (whole-callback `while True` with no break,
    # or an exit that cfg_cleanup proved unreachable) is a non-terminating callback
    # and is rejected. The seed-all backward pass below would compute sound liveness
    # for it regardless; this is a policy rejection, done before any raw allocation
    # so the raise cannot leak.
    cdef bint has_exit = False
    for b in range(nb):
        if blocks[b].edge_count == 0:
            has_exit = True
            break
    if nb > 0 and not has_exit:
        raise ValueError("Infinite loop detected")

    # All raw scratch buffers are allocated up front and checked for NULL in a
    # single combined guard that frees every partial allocation before raising,
    # so no buffer leaks on any OOM path. The L.* fields above are owned by ``L``
    # and freed by __dealloc__; these raw locals are freed at the end (``cursor``
    # is freed early once the CSR is built).
    cdef uint64_t* array_written = <uint64_t*>calloc(<size_t>(nw if nw > 0 else 1), sizeof(uint64_t))
    cdef int32_t* pred_head = <int32_t*>calloc(<size_t>(nb + 1), sizeof(int32_t))
    cdef int32_t* pred_src = <int32_t*>malloc(<size_t>(ne if ne > 0 else 1) * sizeof(int32_t))
    cdef int32_t* cursor = <int32_t*>malloc(<size_t>(nb if nb > 0 else 1) * sizeof(int32_t))
    cdef uint64_t* array_defs_in = <uint64_t*>calloc(<size_t>(<int64_t>nb * nw if nb > 0 and nw > 0 else 1), sizeof(uint64_t))
    cdef uint64_t* acc = <uint64_t*>calloc(<size_t>(nw if nw > 0 else 1), sizeof(uint64_t))
    cdef uint64_t* live = <uint64_t*>calloc(<size_t>(nw if nw > 0 else 1), sizeof(uint64_t))
    cdef uint8_t* visited = <uint8_t*>calloc(<size_t>(nb if nb > 0 else 1), sizeof(uint8_t))
    cdef uint8_t* inq = <uint8_t*>calloc(<size_t>(nb if nb > 0 else 1), sizeof(uint8_t))
    cdef int32_t* stack = <int32_t*>malloc(<size_t>(nb if nb > 0 else 1) * sizeof(int32_t))
    if (array_written == NULL or pred_head == NULL or pred_src == NULL or cursor == NULL
            or array_defs_in == NULL or acc == NULL or live == NULL
            or visited == NULL or inq == NULL or stack == NULL):
        free(array_written); free(pred_head); free(pred_src); free(cursor)
        free(array_defs_in); free(acc); free(live)
        free(visited); free(inq); free(stack)
        raise MemoryError()

    # array_written: arrays with at least one write anywhere in the func. The
    # not-live-before-first-write rule only applies to these; a never-written
    # array that is still read (a legal degenerate case, e.g. a provably-dead
    # VarArray access) must keep ordinary read-liveness back to entry so it
    # interferes with live temps and gets its own slot (reads then observe the
    # never-written -1.0 padding, matching the bump allocators).
    cdef int32_t aw_pid
    for i in range(ni):
        if instrs[i].op == OPX_SET and (instrs[i].flags & FLAG_STMT_ROOT):
            aw_pid = instrs[i].aux
            if places[aw_pid].kind == PLACE_TEMP_ARRAY:
                bs_set(array_written, places[aw_pid].block_ref)

    # Predecessor CSR (pred_head[b]..pred_head[b+1] index into pred_src).
    for e in range(ne):
        pred_head[edges[e].dst + 1] += 1
    for b in range(nb):
        pred_head[b + 1] += pred_head[b]
    for b in range(nb):
        cursor[b] = pred_head[b]
    for e in range(ne):
        pred_src[cursor[edges[e].dst]] = edges[e].src
        cursor[edges[e].dst] += 1
    free(cursor)
    cursor = NULL

    cdef int32_t sp, istart, icount, s, vid, pid, dt, adt, tv, rs
    cdef uint16_t op
    cdef uint8_t kind, side_eff
    cdef bint can_skip, has_defs, dlive, changed

    with nogil:
        # ---- forward array-init pass (from entry) ------------------------
        sp = 0
        stack[sp] = func.entry_block
        sp += 1
        inq[func.entry_block] = 1
        while sp > 0:
            sp -= 1
            b = stack[sp]
            inq[b] = 0
            memcpy(acc, &array_defs_in[<int64_t>b * nw], <size_t>nw * sizeof(uint64_t))
            istart = blocks[b].instr_start
            icount = blocks[b].instr_count
            for i in range(istart, istart + icount):
                if not (instrs[i].flags & FLAG_STMT_ROOT):
                    continue
                if instrs[i].op == OPX_SET:
                    pid = instrs[i].aux
                    if places[pid].kind == PLACE_TEMP_ARRAY:
                        t = places[pid].block_ref
                        if not bs_get(acc, t):
                            L.is_array_init[i] = 1
                            bs_set(acc, t)
                        else:
                            L.is_array_init[i] = 0
            changed = (not visited[b]) or (memcmp(acc, &L.array_defs_out[<int64_t>b * nw],
                                                  <size_t>nw * sizeof(uint64_t)) != 0)
            if changed:
                visited[b] = 1
                memcpy(&L.array_defs_out[<int64_t>b * nw], acc, <size_t>nw * sizeof(uint64_t))
                for e in range(blocks[b].edge_start, blocks[b].edge_start + blocks[b].edge_count):
                    s = edges[e].dst
                    _or_into(&array_defs_in[<int64_t>s * nw], acc, nw)
                    if not inq[s]:
                        stack[sp] = s
                        sp += 1
                        inq[s] = 1

        # ---- backward liveness pass (seeded from all blocks) -------------
        # ``visited`` is reused as ``touched``: whether a block's live_out has been
        # initialized by a successor yet (the first touch counts as a change).
        # Seeding EVERY block -- not only exit-capable ones -- guarantees each is
        # processed at least once, so uses inside an exit-unreachable region (e.g.
        # the body of a conditional infinite loop `if c: while True: use(x)`, which
        # no backward walk from an exit can reach) still propagate to predecessors.
        # The dataflow has a unique fixpoint: exit-reachable blocks converge to the
        # same live sets an exit-only seed would give (identical codegen); only the
        # exit-unreachable blocks, previously left calloc-zero (unsound), change.
        memset(inq, 0, <size_t>(nb if nb > 0 else 1) * sizeof(uint8_t))
        memset(visited, 0, <size_t>(nb if nb > 0 else 1) * sizeof(uint8_t))
        sp = 0
        for b in range(nb):
            stack[sp] = b
            sp += 1
            inq[b] = 1
        while sp > 0:
            sp -= 1
            b = stack[sp]
            inq[b] = 0

            # live := live_out[b] with written arrays not yet defined-on-all-paths
            # dropped (never-written arrays keep ordinary read-liveness).
            for w in range(nw):
                live[w] = L.live_out[<int64_t>b * nw + w] & ~(
                    (L.array_mask[w] & array_written[w]) & ~L.array_defs_out[<int64_t>b * nw + w]
                )

            # Block test counts as a use at block end.
            tv = blocks[b].test_val
            if tv >= 0:
                _gen_value(instrs, args, places, live, tv)

            istart = blocks[b].instr_start
            icount = blocks[b].instr_count
            for i in range(istart + icount - 1, istart - 1, -1):
                if not (instrs[i].flags & FLAG_STMT_ROOT):
                    continue
                rs = L.root_slot[i]
                memcpy(&L.root_live[<int64_t>rs * nw], live, <size_t>nw * sizeof(uint64_t))
                op = instrs[i].op
                if op == OPX_SET:
                    vid = <int32_t>args[instrs[i].arg_start]
                    pid = instrs[i].aux
                    side_eff = instrs[vid].flags & FLAG_SIDE_EFFECT
                    kind = places[pid].kind
                    dt = -1
                    adt = -1
                    if kind == PLACE_TEMP_SCALAR:
                        dt = places[pid].block_ref
                    elif kind == PLACE_TEMP_ARRAY:
                        adt = places[pid].block_ref
                    can_skip = False
                    if not side_eff:
                        has_defs = (dt >= 0) or (adt >= 0)
                        if has_defs:
                            dlive = ((dt >= 0 and bs_get(live, dt)) or
                                     (adt >= 0 and bs_get(live, adt)))
                            if not dlive:
                                can_skip = True
                    if can_skip:
                        continue
                    if dt >= 0:
                        bs_clear(live, dt)
                    if L.is_array_init[i] and adt >= 0:
                        bs_clear(live, adt)
                    _gen_place_write(instrs, args, places, live, pid)
                    _gen_value(instrs, args, places, live, vid)
                else:
                    # Bare side-effecting statement root.
                    _gen_value(instrs, args, places, live, i)

            # live is now live_in[b]; always propagate to predecessors so a
            # first-touch reaches them even when live_in is empty. Re-enqueue a
            # predecessor when it is first touched or its live_out grew.
            memcpy(&L.live_in[<int64_t>b * nw], live, <size_t>nw * sizeof(uint64_t))
            for e in range(pred_head[b], pred_head[b + 1]):
                s = pred_src[e]
                changed = _or_into(&L.live_out[<int64_t>s * nw], live, nw)
                if (not visited[s]) or changed:
                    visited[s] = 1
                    if not inq[s]:
                        stack[sp] = s
                        sp += 1
                        inq[s] = 1

    free(array_defs_in)
    free(array_written)
    free(acc)
    free(live)
    free(visited)
    free(inq)
    free(stack)
    free(pred_head)
    free(pred_src)
    return L


# --------------------------------------------------------------------------
# Python-visible debug entry.
# --------------------------------------------------------------------------

def liveness_debug(entry, mode=None, callback=None):
    """Marshal ``entry`` into an arena, compute liveness, return name-set dicts.

    Returns ``{"live_in", "live_out", "stmt_live", "n_blocks"}`` where the block
    keys are arena block ids (reverse-postorder, matching
    ``traverse_cfg_reverse_postorder(entry)``) and values are sets of the
    original source temp names. ``stmt_live`` maps statement-root instr indices
    to their live-out sets.
    """
    cdef Func func = <Func>marshal_in(entry, mode, callback)
    func.verify()
    cdef Liveness L = compute_liveness(func)
    return {
        "live_in": L.live_in_names(),
        "live_out": L.live_out_names(),
        "stmt_live": L.root_live_names(),
        "array_defs_out": L.array_defs_out_names(),
        "is_array_init": L.is_array_init_map(),
        "n_blocks": L.n_blocks,
    }


# --------------------------------------------------------------------------
# Dominators: Cooper-Harvey-Kennedy iterative idom + dom-tree Euler tour.
# --------------------------------------------------------------------------

cdef class Dominators:
    def __cinit__(self):
        self.func = None
        self.n_blocks = 0
        self.idom = NULL
        self.tin = NULL
        self.tout = NULL
        self.pred_head = NULL
        self.pred_src = NULL
        self.child_head = NULL
        self.child_list = NULL

    def __dealloc__(self):
        free(self.idom)
        free(self.tin)
        free(self.tout)
        free(self.pred_head)
        free(self.pred_src)
        free(self.child_head)
        free(self.child_list)

    cdef bint dominates(self, int32_t a, int32_t b) noexcept nogil:
        # a dominates b iff a's Euler interval encloses b's (reflexive).
        return self.tin[a] <= self.tin[b] and self.tout[b] <= self.tout[a]

    # --- python debug accessors ------------------------------------------

    def idom_map(self):
        cdef dict d = {}
        cdef int32_t b
        for b in range(self.n_blocks):
            d[b] = self.idom[b]
        return d

    def children_map(self):
        cdef dict d = {}
        cdef int32_t b, k
        for b in range(self.n_blocks):
            d[b] = [self.child_list[k] for k in range(self.child_head[b], self.child_head[b + 1])]
        return d

    def dominates_q(self, int a, int b):
        return bool(self.dominates(<int32_t>a, <int32_t>b))


cdef int32_t _dom_intersect(int32_t* idom, int32_t a, int32_t b) noexcept nogil:
    # CHK "intersect": walk the two fingers up (by id == RPO number) to the
    # nearest common dominator.
    while a != b:
        while a > b:
            a = idom[a]
        while b > a:
            b = idom[b]
    return a


cdef Dominators compute_dominators(Func func):
    cdef Dominators D = Dominators()
    D.func = func
    cdef int32_t nb = func.n_blocks
    cdef int32_t ne = func.n_edges
    D.n_blocks = nb

    cdef Edge* edges = func.edges
    cdef int32_t i, b, e, s, p, new_idom, cur

    D.idom = <int32_t*>malloc(<size_t>(nb if nb > 0 else 1) * sizeof(int32_t))
    D.tin = <int32_t*>malloc(<size_t>(nb if nb > 0 else 1) * sizeof(int32_t))
    D.tout = <int32_t*>malloc(<size_t>(nb if nb > 0 else 1) * sizeof(int32_t))
    D.pred_head = <int32_t*>calloc(<size_t>(nb + 1), sizeof(int32_t))
    D.pred_src = <int32_t*>malloc(<size_t>(ne if ne > 0 else 1) * sizeof(int32_t))
    D.child_head = <int32_t*>calloc(<size_t>(nb + 1), sizeof(int32_t))
    D.child_list = <int32_t*>malloc(<size_t>(nb if nb > 0 else 1) * sizeof(int32_t))
    if (D.idom == NULL or D.tin == NULL or D.tout == NULL or D.pred_head == NULL
            or D.pred_src == NULL or D.child_head == NULL or D.child_list == NULL):
        raise MemoryError()

    # Predecessor CSR.
    cdef int32_t* cursor = <int32_t*>malloc(<size_t>(nb if nb > 0 else 1) * sizeof(int32_t))
    if cursor == NULL:
        raise MemoryError()
    for e in range(ne):
        D.pred_head[edges[e].dst + 1] += 1
    for b in range(nb):
        D.pred_head[b + 1] += D.pred_head[b]
    for b in range(nb):
        cursor[b] = D.pred_head[b]
    for e in range(ne):
        D.pred_src[cursor[edges[e].dst]] = edges[e].src
        cursor[edges[e].dst] += 1
    free(cursor)

    # CHK iterative idom over RPO (block id order). entry (0) dominates itself.
    cdef int32_t entry = func.entry_block
    for b in range(nb):
        D.idom[b] = -1
    D.idom[entry] = entry
    cdef bint changed = True
    while changed:
        changed = False
        for b in range(nb):
            if b == entry:
                continue
            new_idom = -1
            for i in range(D.pred_head[b], D.pred_head[b + 1]):
                p = D.pred_src[i]
                if D.idom[p] == -1:
                    continue
                if new_idom == -1:
                    new_idom = p
                else:
                    new_idom = _dom_intersect(D.idom, p, new_idom)
            if new_idom != -1 and D.idom[b] != new_idom:
                D.idom[b] = new_idom
                changed = True

    # Write idom back into the reserved BlockInfo field.
    for b in range(nb):
        func.blocks[b].idom = D.idom[b]

    # Dom-tree child CSR (each non-entry block is a child of its idom).
    for b in range(nb):
        if b != entry and D.idom[b] != -1:
            D.child_head[D.idom[b] + 1] += 1
    for b in range(nb):
        D.child_head[b + 1] += D.child_head[b]
    cdef int32_t* ccur = <int32_t*>malloc(<size_t>(nb if nb > 0 else 1) * sizeof(int32_t))
    if ccur == NULL:
        raise MemoryError()
    for b in range(nb):
        ccur[b] = D.child_head[b]
    for b in range(nb):
        if b != entry and D.idom[b] != -1:
            p = D.idom[b]
            D.child_list[ccur[p]] = b
            ccur[p] += 1
    free(ccur)

    # Euler-tour tin/tout over the dom tree (iterative DFS from entry).
    for b in range(nb):
        D.tin[b] = -1
        D.tout[b] = -1
    cdef int32_t* stack = <int32_t*>malloc(<size_t>(2 * nb if nb > 0 else 1) * sizeof(int32_t))
    cdef int32_t* it = <int32_t*>malloc(<size_t>(nb if nb > 0 else 1) * sizeof(int32_t))
    if stack == NULL or it == NULL:
        free(stack); free(it)
        raise MemoryError()
    cdef int32_t timer = 0
    cdef int32_t sp = 0
    for b in range(nb):
        it[b] = D.child_head[b]
    stack[sp] = entry
    sp += 1
    D.tin[entry] = timer
    timer += 1
    while sp > 0:
        cur = stack[sp - 1]
        if it[cur] < D.child_head[cur + 1]:
            s = D.child_list[it[cur]]
            it[cur] += 1
            D.tin[s] = timer
            timer += 1
            stack[sp] = s
            sp += 1
        else:
            D.tout[cur] = timer
            timer += 1
            sp -= 1
    free(stack)
    free(it)
    return D


def dominators_debug(entry, mode=None, callback=None):
    """Marshal ``entry``, compute dominators, return idom / children / a few queries.

    Returns ``{"idom", "children", "n_blocks", "dominates"}`` keyed by arena block
    id (reverse-postorder). ``dominates`` is the callable O(1) query object.
    """
    cdef Func func = <Func>marshal_in(entry, mode, callback)
    func.verify()
    cdef Dominators D = compute_dominators(func)
    return {
        "idom": D.idom_map(),
        "children": D.children_map(),
        "n_blocks": D.n_blocks,
        "dominates": D.dominates_q,
    }


# --------------------------------------------------------------------------
# Loop forest: natural loops from dominator back edges.
# --------------------------------------------------------------------------

cdef class LoopForest:
    def __cinit__(self):
        self.func = None
        self.n_blocks = 0
        self.n_loops = 0
        self.nwb = 0
        self.depth = NULL
        self.innermost = NULL
        self.header = NULL
        self.parent = NULL
        self.loop_depth = NULL
        self.body = NULL

    def __dealloc__(self):
        free(self.depth)
        free(self.innermost)
        free(self.header)
        free(self.parent)
        free(self.loop_depth)
        free(self.body)

    cdef bint in_loop(self, int32_t loop_id, int32_t block) noexcept nogil:
        return bs_get(&self.body[<int64_t>loop_id * self.nwb], block)

    cdef bint crosses_loop(self, int32_t def_block, int32_t use_block) noexcept nogil:
        # Sinking a def into use_block crosses a loop iff use_block is in some
        # loop that does not contain def_block. Because the loops containing a
        # block form a nested chain, this holds iff def_block is not in
        # use_block's innermost loop.
        cdef int32_t li = self.innermost[use_block]
        if li < 0:
            return False
        return not self.in_loop(li, def_block)

    # --- python debug accessors ------------------------------------------

    def depth_map(self):
        cdef dict d = {}
        cdef int32_t b
        for b in range(self.n_blocks):
            d[b] = self.depth[b]
        return d

    def innermost_map(self):
        cdef dict d = {}
        cdef int32_t b
        for b in range(self.n_blocks):
            d[b] = self.innermost[b]
        return d

    def loops(self):
        # For each loop id: (header, parent, depth, sorted body block ids).
        cdef list out = []
        cdef int32_t li, b
        for li in range(self.n_loops):
            members = [b for b in range(self.n_blocks) if bs_get(&self.body[li * self.nwb], b)]
            out.append((self.header[li], self.parent[li], self.loop_depth[li], members))
        return out

    def crosses_loop_q(self, int def_block, int use_block):
        return bool(self.crosses_loop(<int32_t>def_block, <int32_t>use_block))


cdef LoopForest compute_loops(Func func, Dominators D):
    cdef LoopForest F = LoopForest()
    F.func = func
    cdef int32_t nb = func.n_blocks
    cdef int32_t ne = func.n_edges
    cdef int32_t nwb = (nb + 63) >> 6
    F.n_blocks = nb
    F.nwb = nwb

    cdef Edge* edges = func.edges
    cdef int32_t e, b, u, h, li, lj, p, w

    # Header blocks: a block h with a back edge u->h (h dominates u). Deterministic
    # loop ids assigned in ascending header-block order.
    cdef uint8_t* is_header = <uint8_t*>calloc(<size_t>(nb if nb > 0 else 1), sizeof(uint8_t))
    if is_header == NULL:
        raise MemoryError()
    for e in range(ne):
        u = edges[e].src
        h = edges[e].dst
        if D.dominates(h, u):
            is_header[h] = 1

    cdef int32_t n_loops = 0
    for b in range(nb):
        if is_header[b]:
            n_loops += 1
    F.n_loops = n_loops

    F.depth = <int32_t*>calloc(<size_t>(nb if nb > 0 else 1), sizeof(int32_t))
    F.innermost = <int32_t*>malloc(<size_t>(nb if nb > 0 else 1) * sizeof(int32_t))
    F.header = <int32_t*>malloc(<size_t>(n_loops if n_loops > 0 else 1) * sizeof(int32_t))
    F.parent = <int32_t*>malloc(<size_t>(n_loops if n_loops > 0 else 1) * sizeof(int32_t))
    F.loop_depth = <int32_t*>calloc(<size_t>(n_loops if n_loops > 0 else 1), sizeof(int32_t))
    F.body = <uint64_t*>calloc(<size_t>(<int64_t>n_loops * nwb if n_loops > 0 and nwb > 0 else 1), sizeof(uint64_t))
    if (F.depth == NULL or F.innermost == NULL or F.header == NULL or F.parent == NULL
            or F.loop_depth == NULL or F.body == NULL):
        free(is_header)
        raise MemoryError()
    for b in range(nb):
        F.innermost[b] = -1

    # loop id per header block (ascending header order == ascending block id).
    cdef int32_t* loop_of_header = <int32_t*>malloc(<size_t>(nb if nb > 0 else 1) * sizeof(int32_t))
    if loop_of_header == NULL:
        free(is_header)
        raise MemoryError()
    for b in range(nb):
        loop_of_header[b] = -1
    li = 0
    for b in range(nb):
        if is_header[b]:
            loop_of_header[b] = li
            F.header[li] = b
            F.parent[li] = -1
            li += 1

    # Predecessor CSR for backward reachability.
    cdef int32_t* pred_head = <int32_t*>calloc(<size_t>(nb + 1), sizeof(int32_t))
    cdef int32_t* pred_src = <int32_t*>malloc(<size_t>(ne if ne > 0 else 1) * sizeof(int32_t))
    cdef int32_t* cursor = <int32_t*>malloc(<size_t>(nb if nb > 0 else 1) * sizeof(int32_t))
    cdef int32_t* stack = <int32_t*>malloc(<size_t>(nb if nb > 0 else 1) * sizeof(int32_t))
    if pred_head == NULL or pred_src == NULL or cursor == NULL or stack == NULL:
        free(is_header); free(loop_of_header); free(pred_head); free(pred_src); free(cursor); free(stack)
        raise MemoryError()
    for e in range(ne):
        pred_head[edges[e].dst + 1] += 1
    for b in range(nb):
        pred_head[b + 1] += pred_head[b]
    for b in range(nb):
        cursor[b] = pred_head[b]
    for e in range(ne):
        pred_src[cursor[edges[e].dst]] = edges[e].src
        cursor[edges[e].dst] += 1

    # Body of each loop: header + backward reach from every latch, stopping at
    # the header (which is pre-seeded, so its own predecessors are not added).
    cdef uint64_t* lbody
    cdef int32_t sp
    for e in range(ne):
        u = edges[e].src
        h = edges[e].dst
        if not D.dominates(h, u):
            continue
        li = loop_of_header[h]
        lbody = &F.body[<int64_t>li * nwb]
        bs_set(lbody, h)
        if not bs_get(lbody, u):
            bs_set(lbody, u)
            sp = 0
            stack[sp] = u
            sp += 1
            while sp > 0:
                sp -= 1
                b = stack[sp]
                for p in range(pred_head[b], pred_head[b + 1]):
                    w = pred_src[p]
                    if not bs_get(lbody, w):
                        bs_set(lbody, w)
                        stack[sp] = w
                        sp += 1

    # Loop depth = number of loops containing a loop's header (including itself).
    cdef int32_t di
    for li in range(n_loops):
        di = 0
        for lj in range(n_loops):
            if bs_get(&F.body[<int64_t>lj * nwb], F.header[li]):
                di += 1
        F.loop_depth[li] = di

    # Parent loop of L = the loop (other than L) containing L's header with the
    # greatest depth (the immediately-enclosing loop).
    cdef int32_t best, best_depth
    for li in range(n_loops):
        best = -1
        best_depth = 0
        for lj in range(n_loops):
            if lj == li:
                continue
            if bs_get(&F.body[<int64_t>lj * nwb], F.header[li]):
                if F.loop_depth[lj] > best_depth:
                    best_depth = F.loop_depth[lj]
                    best = lj
        F.parent[li] = best

    # Per-block innermost loop (max depth) and depth (count of containing loops).
    cdef int32_t cnt, bi
    for b in range(nb):
        cnt = 0
        best = -1
        best_depth = 0
        for li in range(n_loops):
            if bs_get(&F.body[<int64_t>li * nwb], b):
                cnt += 1
                if F.loop_depth[li] > best_depth:
                    best_depth = F.loop_depth[li]
                    best = li
        F.depth[b] = cnt
        F.innermost[b] = best

    free(is_header)
    free(loop_of_header)
    free(pred_head)
    free(pred_src)
    free(cursor)
    free(stack)
    return F


def loops_debug(entry, mode=None, callback=None):
    """Marshal ``entry``, compute dominators + loop forest, return a summary dict.

    Keyed by arena block id (reverse-postorder). ``crosses`` is the O(1)
    ``crosses_loop(def_block, use_block)`` query used by treeify.
    """
    cdef Func func = <Func>marshal_in(entry, mode, callback)
    func.verify()
    cdef Dominators D = compute_dominators(func)
    cdef LoopForest F = compute_loops(func, D)
    return {
        "depth": F.depth_map(),
        "innermost": F.innermost_map(),
        "loops": F.loops(),
        "n_blocks": F.n_blocks,
        "n_loops": F.n_loops,
        "crosses": F.crosses_loop_q,
    }
