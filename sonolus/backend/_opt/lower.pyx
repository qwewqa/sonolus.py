# cython: language_level=3
"""Out-of-SSA lowering + treeify, then temp-memory allocation (§7.4, §7.5).

Two layers live here:

* ``lower_from_ssa`` (§7.4) -- the real out-of-SSA + treeify that supersedes the
  naive ``midend.out_of_ssa``. It takes a value-based SSA ``Func`` (from
  ``build_ssa``) and produces a non-SSA §3-legal arena ready for
  ``allocate_func`` + ``emit_func``. See ``_Lower`` below for the full contract.
* ``allocate_func`` (§7.5) -- rewrites every temp place (size-1 scalar, size>1
  array, size-0 placeholder) to a ``BlockPlace(10000, ...)`` real-block place,
  with three strategies:

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

from libc.stdint cimport int16_t, int32_t, uint8_t, uint16_t, uint32_t, uint64_t
from libc.stdlib cimport calloc, free, malloc, realloc
from libc.string cimport memcpy

from sonolus.backend._opt.ir cimport (
    BlockInfo,
    Edge,
    EDGE_COND_NONE,
    EDGE_COND_VALUE,
    FLAG_CONST_IS_INT,
    FLAG_PINNED,
    FLAG_PURE,
    FLAG_SIDE_EFFECT,
    FLAG_STMT_ROOT,
    Func,
    Instr,
    PLACE_DYNAMIC_BLOCK,
    PLACE_REAL_BLOCK,
    PLACE_RUNTIME_CONST,
    PLACE_TEMP_ARRAY,
    PLACE_TEMP_SCALAR,
    PLACE_TEMP_SIZE0,
    PLACE_WRITABLE,
    PlaceInfo,
    TempInfo,
)
from sonolus.backend._opt._ops_gen cimport (
    OP_Add,
    OP_Divide,
    OP_Mod,
    OP_Multiply,
    OP_Negate,
    OP_Rem,
    OP_RUNTIME_COUNT,
    OP_Subtract,
    OPX_CONST,
    OPX_GET,
    OPX_PHI,
    OPX_SET,
    OPX_UNDEF,
)
from sonolus.backend._opt.analysis cimport (
    Dominators,
    Liveness,
    LoopForest,
    bs_get,
    bs_set,
    compute_dominators,
    compute_liveness,
    compute_loops,
)
from sonolus.backend._opt.midend cimport build_ssa, cfg_cleanup

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


# ==========================================================================
# Out-of-SSA + treeify (OPTIMIZER_REWRITE.md 7.4). Supersedes the naive
# midend.out_of_ssa. Consumes a value-based SSA Func (build_ssa), produces a
# non-SSA 3-legal arena for allocate_func + emit_func.
#
# Pipeline (lower_from_ssa):
#   1-3. _Lower.build()   scheduling decision (7.4.1) + phi elimination (7.4.2)
#                         + tree emission with n-ary flatten & identity dropping
#                         (7.4.3), producing a fresh non-SSA arena.
#   4.   _coalesce()      final-schedule liveness -> interference -> coalesce
#                         phi-copy / copy-related webs; delete self-copies (7.4.4).
#   5.   cfg_cleanup(phi_safe=True) -> RPO layout, then _normalize_switch (7.4.5).
#
# SCHEDULING SEMANTICS actually implemented (7.4.1), per SSA value v:
#
# * Use positions: a normal operand use is at its consumer instruction; a block
#   test use is at the block END; a phi operand use is at the END of the
#   corresponding predecessor edge (per the incoming-edge contract).
# * ``rematerialisable/inlinable-anywhere`` (``inlinable[v]``): recomputable at
#   any point v dominates. Structurally: OPX_CONST; a non-writable real-block
#   OPX_GET with a constant or itself-stable index; a pure op all of whose
#   operands are stable (materialised temp / phi / const / undef) or themselves
#   inlinable. Writable/dynamic/array reads, Random, and side-effecting values
#   are NOT inlinable. (Decision-aware: computed bottom-up in value-id order, so
#   a materialised operand counts as a stable leaf.)
# * ``runtime-constant tree`` (``_rtc``): pure ops over OPX_CONST + PLACE_
#   RUNTIME_CONST reads (the marshal-in flag). ALWAYS folded/duplicated, never
#   materialised and never gated by loop-crossing -- a temp defeats the runtime's
#   own constant folding (effective cost 1, section 2), so it duplicates
#   regardless of size, including into phi copies (safe: no phi/temp reads).
# * Decision (skipping consts/undef/phis/roots):
#     - use_count==0 & not side-effecting        -> DROP.
#     - side-effecting non-root                  -> MATERIALISE (never fold an
#                                                    effect; matches the naive
#                                                    lowering exactly).
#     - runtime-constant tree                    -> FOLD/DUP (never materialise).
#     - used by a phi (and not runtime-const)    -> MATERIALISE (phi operands are
#                                                    a temp or a runtime-const
#                                                    tree, so coalescing owns the
#                                                    copy web and no folded tree
#                                                    can read a phi temp / form a
#                                                    lowering cycle).
#     - inlinable[v]:
#         single-use  -> FOLD unless crosses_loop(def, use) (sinking into a
#                        deeper loop; then MATERIALISE, hoisting the single eval).
#         multi-use   -> DUPLICATE iff decision-aware tree cost < 4 (a
#                        materialised operand costs 3, a scalar-temp get; the
#                        threshold reproduces the old CSE cost>=4 extraction and
#                        InlineVars leaving cheap exprs duplicated), else
#                        MATERIALISE.
#     - not inlinable (pinned OPX_GET of a writable/dynamic block, or a pure op
#       over such a read):
#         FOLD only in the conservative same-block, single-use,
#         no-effectful-instruction-between-def-and-use case (Case B; the effect-
#         free ranges chain transitively, so this composes); else MATERIALISE.
#         Never across a loop back edge (same-block => same loop). Random/other
#         pinned non-GET values never take this path -> MATERIALISE.
# * set_cost derivation for the cost comparison ``dup_cost*uses <
#   set_cost + get_cost*uses``: get_cost = scalar-temp get = 3 (section 2); the
#   materialise side is ``tree_cost + get_cost*uses`` (compute once, read each
#   use -- the temp write is amortised to zero, exactly as the old CSE SSA
#   extraction), and duplication is ``tree_cost*uses``, so break-even is
#   ``get_cost < tree_cost`` <=> ``tree_cost >= 4``, independent of uses -> the
#   constant-4 threshold above.
#
# DELIBERATE DIVERGENCE (7.4.1): constant-index reads of WRITABLE blocks are
# never duplicated (they are not inlinable, so multi-use materialises). Old
# InlineVars' alias path duplicated them freely regardless of writability;
# duplicating across an intervening write could observe it, so this is stricter.
# ==========================================================================


cdef TempInfo* _grow_temps_l(TempInfo* buf, int32_t* cap, int32_t need) except NULL:
    cdef int32_t nc
    cdef TempInfo* p
    if need <= cap[0]:
        return buf
    nc = cap[0] if cap[0] > 0 else 8
    while nc < need:
        nc *= 2
    p = <TempInfo*>realloc(buf, <size_t>nc * sizeof(TempInfo))
    if p == NULL:
        raise MemoryError()
    cap[0] = nc
    return p


cdef PlaceInfo* _grow_places_l(PlaceInfo* buf, int32_t* cap, int32_t need) except NULL:
    cdef int32_t nc
    cdef PlaceInfo* p
    if need <= cap[0]:
        return buf
    nc = cap[0] if cap[0] > 0 else 8
    while nc < need:
        nc *= 2
    p = <PlaceInfo*>realloc(buf, <size_t>nc * sizeof(PlaceInfo))
    if p == NULL:
        raise MemoryError()
    cap[0] = nc
    return p


cdef int32_t _add_place_l(Func dst, uint8_t kind, uint8_t flags, int32_t block_ref,
                          int32_t index_val, int32_t offset) except -1:
    cdef int32_t pid = dst.n_places
    dst.places = _grow_places_l(dst.places, &dst.cap_places, pid + 1)
    dst.places[pid].kind = kind
    dst.places[pid].flags = flags
    dst.places[pid].block_ref = block_ref
    dst.places[pid].index_val = index_val
    dst.places[pid].offset = offset
    dst.n_places = pid + 1
    return pid


def _seq_parallel_copies(list copies, make_fresh):
    """Sequentialize a parallel copy set (Boissinot et al.) with fresh cycle temps.

    ``copies`` is a list of ``(dst, src)`` with distinct ``dst`` keys (parallel
    semantics: all reads happen before all writes). ``make_fresh()`` yields a
    fresh temp id. ``dst`` keys are temp ids (ints); ``src`` keys are ints
    (temps) or tuples (const / undef / runtime-const-tree leaves, never dsts).
    """
    cdef list pcopy = [(d, s) for (d, s) in copies if d != s]
    cdef list result = []
    if not pcopy:
        return result
    src_set = set(s for (_d, s) in pcopy)
    loc = {}
    pred = {}
    to_do = []
    for (d, s) in pcopy:
        loc[s] = s
        pred[d] = s
        to_do.append(d)
    ready = [d for (d, s) in pcopy if d not in src_set]
    while to_do:
        while ready:
            d = ready.pop()
            if d not in pred:
                continue
            s = pred[d]
            c = loc[s]
            result.append((d, c))
            loc[s] = d
            del pred[d]
            if s == c and s in pred:
                ready.append(s)
        if not to_do:
            break
        d = to_do.pop()
        if d not in pred:
            continue
        if loc[pred[d]] != d:
            n = make_fresh()
            result.append((n, d))
            loc[d] = n
            ready.append(d)
        else:
            del pred[d]
    return result


# Use-position sentinels (a value's recorded use site).
DEF _TEST_USE = -2   # used by a block test: use position is the block END.
DEF _PHI_USE = -3    # used as a phi operand: cross-block, Case B never applies.


cdef class _Lower:
    cdef Func src
    cdef Func dst
    cdef int32_t nb
    cdef int32_t n_out
    # use analysis (per SSA value id)
    cdef list use_count
    cdef list use_block
    cdef list use_pos
    cdef list used_by_phi
    cdef list materialize
    cdef list drop
    cdef list inlinable
    cdef list value_temp
    cdef list rtc_memo
    cdef list tc_memo
    # topology
    cdef list incoming          # per block -> edge ids (ascending == contract)
    cdef list edge_pos          # per edge -> its index in incoming[dst]
    cdef list distinct_succ     # per block -> sorted distinct successor blocks
    cdef list edge_split        # per edge -> split block id (-1 if not split)
    cdef list split_edge        # per split block -> its source edge id
    # temps / places
    cdef dict array_temp_map
    cdef dict scalar_place
    cdef int32_t undef_temp
    cdef int32_t undef_place
    cdef int32_t name_counter
    # analyses
    cdef Dominators doms
    cdef LoopForest loops

    def __cinit__(self, Func src):
        self.src = src
        self.dst = Func()
        self.nb = src.n_blocks
        self.array_temp_map = {}
        self.scalar_place = {}
        self.undef_temp = -1
        self.undef_place = -1
        self.name_counter = 0
        cdef int32_t ni = src.n_instrs
        self.use_count = [0] * ni
        self.use_block = [-1] * ni
        self.use_pos = [-1] * ni
        self.used_by_phi = [False] * ni
        self.materialize = [False] * ni
        self.drop = [False] * ni
        self.inlinable = [False] * ni
        self.value_temp = [-1] * ni
        self.rtc_memo = [None] * ni
        self.tc_memo = [None] * ni
        cdef int32_t e
        self.incoming = [[] for _ in range(self.nb)]
        self.edge_pos = [0] * src.n_edges
        for e in range(src.n_edges):
            lst = <list>self.incoming[src.edges[e].dst]
            self.edge_pos[e] = len(lst)
            lst.append(e)
        cdef list ss = [set() for _ in range(self.nb)]
        for e in range(src.n_edges):
            (<set>ss[src.edges[e].src]).add(src.edges[e].dst)
        self.distinct_succ = [sorted(s) for s in ss]
        self.edge_split = [-1] * src.n_edges
        self.split_edge = []
        self.doms = compute_dominators(src)
        self.loops = compute_loops(src, self.doms)

    # -- runtime-constant / inlinable / cost analyses ----------------------

    def _rtc(self, int32_t v):
        # Runtime-constant tree: pure ops over OPX_CONST + PLACE_RUNTIME_CONST
        # reads. A materialised / phi / undef operand emits as a temp read (never
        # runtime-constant), which also STOPS recursion at materialised values --
        # essential because an uninitialised self-referential loop variable makes
        # the SSA value graph cyclic (phi(UNDEF,v)=v collapse), and the cycle's
        # back edge is always a materialised undef-widened value. Memoized.
        cached = self.rtc_memo[v]
        if cached is not None:
            return <bint>cached
        cdef Instr* ins = &self.src.instrs[v]
        cdef int32_t op = ins.op
        cdef int32_t astart, nargs, k, o, oop
        cdef bint r
        if op == OPX_CONST:
            r = True
        elif op == OPX_GET:
            r = (self.src.places[ins.aux].flags & PLACE_RUNTIME_CONST) != 0
        elif op < OP_RUNTIME_COUNT and (ins.flags & FLAG_PURE):
            r = True
            astart = ins.arg_start
            nargs = ins.nargs
            for k in range(nargs):
                o = <int32_t>self.src.args[astart + k]
                oop = self.src.instrs[o].op
                if oop == OPX_PHI or oop == OPX_UNDEF or <bint>self.materialize[o]:
                    r = False
                    break
                if not self._rtc(o):
                    r = False
                    break
        else:
            r = False
        self.rtc_memo[v] = r
        return r

    def _operand_stable(self, int32_t o):
        # An operand emitted as a stable leaf (temp read / const) OR a folded
        # inlinable tree -> safe to recompute anywhere its parent is recomputed.
        cdef int32_t op = self.src.instrs[o].op
        if op == OPX_CONST or op == OPX_UNDEF or op == OPX_PHI:
            return True
        if <bint>self.drop[o]:
            return True
        if <bint>self.materialize[o]:
            return True
        return <bint>self.inlinable[o]

    def _compute_inlinable(self, int32_t v):
        # inlinable-anywhere for v, reading already-decided operand info.
        cdef Instr* ins = &self.src.instrs[v]
        cdef int32_t op = ins.op
        cdef int32_t pid, kind, iv, astart, nargs, k
        if op == OPX_CONST:
            return True
        if op == OPX_GET:
            pid = ins.aux
            kind = self.src.places[pid].kind
            if kind != PLACE_REAL_BLOCK or (self.src.places[pid].flags & PLACE_WRITABLE):
                return False
            iv = self.src.places[pid].index_val
            return (iv < 0) or <bint>self._operand_stable(iv)
        if op < OP_RUNTIME_COUNT and (ins.flags & FLAG_PURE):
            astart = ins.arg_start
            nargs = ins.nargs
            for k in range(nargs):
                if not <bint>self._operand_stable(<int32_t>self.src.args[astart + k]):
                    return False
            return True
        return False

    def _op_cost(self, int32_t o):
        # Cost of operand o as it will be emitted (decision-aware).
        cdef int32_t op = self.src.instrs[o].op
        if op == OPX_CONST:
            return 1
        if op == OPX_UNDEF or op == OPX_PHI:
            return 3
        if <bint>self.materialize[o]:
            return 3
        return self._tree_cost(o)

    def _tree_cost(self, int32_t v):
        cached = self.tc_memo[v]
        if cached is not None:
            return <int32_t>cached
        cdef int32_t r
        if self._rtc(v):
            self.tc_memo[v] = 1
            return 1
        cdef Instr* ins = &self.src.instrs[v]
        cdef int32_t op = ins.op
        cdef int32_t pid, kind, iv, astart, nargs, k
        if op == OPX_CONST:
            r = 1
        elif op == OPX_GET:
            pid = ins.aux
            kind = self.src.places[pid].kind
            if kind == PLACE_DYNAMIC_BLOCK:
                r = 1 + self._op_cost(self.src.places[pid].block_ref)
            else:
                r = 2  # Get func + block push
            iv = self.src.places[pid].index_val
            if iv < 0:
                r += 1
            else:
                r += self._op_cost(iv)
        elif op < OP_RUNTIME_COUNT and (ins.flags & FLAG_PURE):
            r = 1
            astart = ins.arg_start
            nargs = ins.nargs
            for k in range(nargs):
                r += self._op_cost(<int32_t>self.src.args[astart + k])
        else:
            r = 3
        self.tc_memo[v] = r
        return r

    def _no_effect_between(self, int32_t v, int32_t upos):
        # No FLAG_SIDE_EFFECT instruction strictly between v and its use (same
        # block). For a test use, scan to the block end.
        cdef Instr* instrs = self.src.instrs
        cdef int32_t b = instrs[v].block
        cdef int32_t lo = v + 1
        cdef int32_t hi, i
        if upos == _TEST_USE:
            hi = self.src.blocks[b].instr_start + self.src.blocks[b].instr_count
        elif upos >= 0:
            hi = upos
        else:
            return False
        for i in range(lo, hi):
            if instrs[i].flags & FLAG_SIDE_EFFECT:
                return False
        return True

    # -- use analysis ------------------------------------------------------

    def _record_use(self, int32_t v, int32_t ublock, int32_t upos, bint is_phi):
        self.use_count[v] = <int32_t>self.use_count[v] + 1
        self.use_block[v] = ublock
        self.use_pos[v] = upos
        if is_phi:
            self.used_by_phi[v] = True

    def _analyze(self):
        cdef Func src = self.src
        cdef int32_t i, b, op, k, astart, nargs, e, pid, tv, uc
        cdef bint side, ih, single, opk
        # Phase 1: use counts + positions.
        for i in range(src.n_instrs):
            op = src.instrs[i].op
            b = src.instrs[i].block
            if op == OPX_PHI:
                astart = src.instrs[i].arg_start
                for k in range(src.instrs[i].nargs):
                    e = <int32_t>(<list>self.incoming[b])[k]
                    self._record_use(<int32_t>src.args[astart + k], src.edges[e].src, _PHI_USE, True)
                continue
            if op == OPX_UNDEF or op == OPX_CONST:
                continue
            astart = src.instrs[i].arg_start
            nargs = src.instrs[i].nargs
            for k in range(nargs):
                self._record_use(<int32_t>src.args[astart + k], b, i, False)
            if op == OPX_GET or op == OPX_SET:
                pid = src.instrs[i].aux
                if src.places[pid].kind == PLACE_DYNAMIC_BLOCK:
                    self._record_use(src.places[pid].block_ref, b, i, False)
                if src.places[pid].index_val >= 0:
                    self._record_use(src.places[pid].index_val, b, i, False)
        for b in range(self.nb):
            if src.blocks[b].test_val >= 0:
                self._record_use(src.blocks[b].test_val, b, _TEST_USE, False)

        # Values used outside their strict dominance region (from phi(UNDEF,v)=v
        # collapses in build_ssa) MUST be materialised: on the undef path the def
        # has not executed, so the reference reads an uninitialised temp; folding
        # or duplicating would evaluate the value there instead. Force a temp.
        # These are also the ONLY non-phi operands that may reference a LATER value
        # (a loop back edge), and an uninitialised self-referential loop variable
        # makes the value graph cyclic through exactly such an edge -- so
        # pre-marking them materialised (before any _rtc / _tree_cost recursion)
        # both keeps semantics and breaks the cycle at a materialised leaf.
        undef_set = src._ssa_undef if src._ssa_undef is not None else set()
        for i in undef_set:
            op = src.instrs[i].op
            if (op != OPX_PHI and op != OPX_CONST and op != OPX_UNDEF
                    and not (src.instrs[i].flags & FLAG_STMT_ROOT)):
                self.materialize[i] = True

        # Phase 2: scheduling decision, in ascending value-id order so that an
        # operand's decision is known when its consumer is decided.
        for i in range(src.n_instrs):
            op = src.instrs[i].op
            if op == OPX_PHI or op == OPX_CONST or op == OPX_UNDEF:
                continue
            if src.instrs[i].flags & FLAG_STMT_ROOT:
                continue
            if <bint>self.materialize[i]:  # pre-marked undef-widened value
                continue
            uc = <int32_t>self.use_count[i]
            side = (src.instrs[i].flags & FLAG_SIDE_EFFECT) != 0
            if uc == 0 and not side:
                self.drop[i] = True
                continue
            self.inlinable[i] = self._compute_inlinable(i)
            if side:
                self.materialize[i] = True
                continue
            if self._rtc(i):
                self.materialize[i] = False
                continue
            if <bint>self.used_by_phi[i]:
                self.materialize[i] = True
                continue
            ih = <bint>self.inlinable[i]
            single = (uc == 1)
            if ih:
                if single:
                    self.materialize[i] = self.loops.crosses_loop(
                        src.instrs[i].block, <int32_t>self.use_block[i]
                    )
                else:
                    self.materialize[i] = not (self._tree_cost(i) < 4)
            else:
                opk = (op == OPX_GET) or ((src.instrs[i].flags & FLAG_PURE) != 0)
                if (single and opk
                        and <int32_t>self.use_block[i] == src.instrs[i].block
                        and self._no_effect_between(i, <int32_t>self.use_pos[i])):
                    self.materialize[i] = False
                else:
                    self.materialize[i] = True

    # -- temps / places ----------------------------------------------------

    def _new_temp(self, int32_t size):
        cdef Func dst = self.dst
        cdef int32_t tid = dst.n_temps
        dst.temps = _grow_temps_l(dst.temps, &dst.cap_temps, tid + 1)
        cdef int32_t name_id = len(dst.names)
        dst.names.append(f"s{self.name_counter}")
        self.name_counter += 1
        dst.temps[tid].name_id = name_id
        dst.temps[tid].size = size
        dst.n_temps = tid + 1
        return tid

    def _scalar_place_of(self, int32_t temp):
        cached = self.scalar_place.get(temp)
        if cached is not None:
            return <int32_t>cached
        cdef int32_t pid = _add_place_l(self.dst, PLACE_TEMP_SCALAR, PLACE_WRITABLE, temp, -1, 0)
        self.scalar_place[temp] = pid
        return pid

    def _get_undef_place(self):
        if self.undef_place < 0:
            self.undef_temp = self._new_temp(1)
            self.undef_place = self._scalar_place_of(self.undef_temp)
        return self.undef_place

    def _map_array_temp(self, int32_t old):
        cached = self.array_temp_map.get(old)
        if cached is not None:
            return <int32_t>cached
        cdef Func dst = self.dst
        cdef int32_t tid = dst.n_temps
        dst.temps = _grow_temps_l(dst.temps, &dst.cap_temps, tid + 1)
        cdef int32_t name_id = len(dst.names)
        dst.names.append(self.src.names[self.src.temps[old].name_id])
        dst.temps[tid].name_id = name_id
        dst.temps[tid].size = self.src.temps[old].size
        dst.n_temps = tid + 1
        self.array_temp_map[old] = tid
        return tid

    def _new_place(self, int32_t src_pid, int32_t block):
        cdef Func src = self.src
        cdef int32_t kind = src.places[src_pid].kind
        cdef int32_t flags = src.places[src_pid].flags
        cdef int32_t br = src.places[src_pid].block_ref
        cdef int32_t iv = src.places[src_pid].index_val
        cdef int32_t off = src.places[src_pid].offset
        if kind == PLACE_TEMP_ARRAY or kind == PLACE_TEMP_SIZE0:
            br = self._map_array_temp(br)
        elif kind == PLACE_DYNAMIC_BLOCK:
            if src.instrs[br].op == OPX_CONST:
                kind = PLACE_REAL_BLOCK
                flags = 0
                br = <int32_t>int(src.consts[src.instrs[br].aux])
            else:
                br = self._emit_ref(br, block)
        if iv >= 0:
            iv = self._emit_ref(iv, block)
        return _add_place_l(self.dst, <uint8_t>kind, <uint8_t>flags, br, iv, off)

    # -- tree emission (7.4.3) --------------------------------------------

    def _emit_ref(self, int32_t v, int32_t block):
        cdef Func src = self.src
        cdef int32_t op = src.instrs[v].op
        if op == OPX_CONST:
            return self.dst._emit(OPX_CONST, src.instrs[v].flags, block, src.instrs[v].aux, [])
        if op == OPX_UNDEF:
            return self.dst._emit(OPX_GET, FLAG_PINNED, block, self._get_undef_place(), [])
        if op == OPX_PHI or <bint>self.materialize[v]:
            return self.dst._emit(OPX_GET, FLAG_PINNED, block,
                                  self._scalar_place_of(<int32_t>self.value_temp[v]), [])
        return self._emit_tree(v, block)

    def _emit_tree(self, int32_t v, int32_t block):
        cdef Func src = self.src
        cdef int32_t op = src.instrs[v].op
        cdef int32_t astart, nargs, k, pid
        cdef list args
        if op == OPX_GET:
            pid = self._new_place(src.instrs[v].aux, block)
            return self.dst._emit(OPX_GET, src.instrs[v].flags, block, pid, [])
        astart = src.instrs[v].arg_start
        nargs = src.instrs[v].nargs
        args = [self._emit_ref(<int32_t>src.args[astart + k], block) for k in range(nargs)]
        return self._emit_op(op, src.instrs[v].flags, args, block)

    def _is_dst_const(self, int32_t a, double val):
        cdef Func dst = self.dst
        if dst.instrs[a].op != OPX_CONST:
            return False
        return dst.consts[dst.instrs[a].aux] == val

    def _dst_const(self, double val, int32_t block):
        cdef int32_t cid = self.dst._intern_const(val)
        return self.dst._emit(OPX_CONST, FLAG_PURE | FLAG_CONST_IS_INT, block, cid, [])

    def _emit_op(self, int32_t op, int32_t flags, list args, int32_t block):
        # Flatten associative left spines (Add/Multiply/Mod/Rem, args[0] only) and
        # re-apply n-ary identity dropping (RemoveRedundantArguments semantics).
        cdef Func dst = self.dst
        cdef int32_t a0, k, na
        cdef list rest
        if op == OP_Add or op == OP_Multiply or op == OP_Mod or op == OP_Rem:
            if len(args) > 0 and dst.instrs[<int32_t>args[0]].op == op:
                a0 = <int32_t>args[0]
                na = dst.instrs[a0].nargs
                args = [<int32_t>dst.args[dst.instrs[a0].arg_start + k] for k in range(na)] + args[1:]
        if op == OP_Add:
            args = [a for a in args if not self._is_dst_const(<int32_t>a, 0.0)]
            if len(args) == 1:
                return <int32_t>args[0]
            if len(args) == 0:
                return self._dst_const(0.0, block)
        elif op == OP_Subtract:
            rest = [a for a in args[1:] if not self._is_dst_const(<int32_t>a, 0.0)]
            args = [args[0]] + rest
            if len(args) == 1:
                return <int32_t>args[0]
            if len(args) == 2 and self._is_dst_const(<int32_t>args[0], 0.0):
                return self._emit_op(OP_Negate, FLAG_PURE, [args[1]], block)
        elif op == OP_Multiply:
            args = [a for a in args if not self._is_dst_const(<int32_t>a, 1.0)]
            if len(args) == 1:
                return <int32_t>args[0]
            if len(args) == 0:
                return self._dst_const(1.0, block)
        elif op == OP_Divide:
            rest = [a for a in args[1:] if not self._is_dst_const(<int32_t>a, 1.0)]
            args = [args[0]] + rest
            if len(args) == 1:
                return <int32_t>args[0]
        return self.dst._emit(<uint16_t>op, <uint8_t>flags, block, -1, args)

    def _emit_tree_root(self, int32_t v, int32_t block):
        cdef Func src = self.src
        cdef int32_t astart = src.instrs[v].arg_start
        cdef int32_t nargs = src.instrs[v].nargs
        cdef int32_t k
        cdef list args = [self._emit_ref(<int32_t>src.args[astart + k], block) for k in range(nargs)]
        cdef int32_t r = self.dst._emit(src.instrs[v].op, src.instrs[v].flags, block, -1, args)
        self.dst.instrs[r].flags = <uint8_t>(self.dst.instrs[r].flags | FLAG_STMT_ROOT)

    # -- phi copies (7.4.2) ------------------------------------------------

    def _make_cycle_temp(self):
        return self._new_temp(1)

    def _emit_copies(self, int32_t edge, int32_t block):
        cdef Func src = self.src
        cdef int32_t d = src.edges[edge].dst
        cdef int32_t k = <int32_t>self.edge_pos[edge]
        cdef int32_t pstart = src.blocks[d].phi_start
        cdef int32_t pcount = src.blocks[d].phi_count
        cdef int32_t pi, operand, opnd_op, dtemp, ref, dplace
        cdef list copies = []
        for pi in range(pstart, pstart + pcount):
            if <int32_t>self.use_count[pi] == 0:
                continue
            operand = <int32_t>src.args[src.instrs[pi].arg_start + k]
            opnd_op = src.instrs[operand].op
            if opnd_op == OPX_CONST:
                skey = ("c", src.instrs[operand].aux, <int32_t>src.instrs[operand].flags)
            elif opnd_op == OPX_UNDEF:
                skey = ("u",)
            elif self._rtc(operand) and not <bint>self.materialize[operand]:
                skey = ("t", operand)
            else:
                skey = <int32_t>self.value_temp[operand]
            dtemp = <int32_t>self.value_temp[pi]
            copies.append((dtemp, skey))
        cdef list seq = _seq_parallel_copies(copies, self._make_cycle_temp)
        for dtemp, skey in seq:
            dplace = self._scalar_place_of(<int32_t>dtemp)
            if isinstance(skey, tuple):
                if skey[0] == "c":
                    ref = self.dst._emit(OPX_CONST, <int32_t>skey[2], block, <int32_t>skey[1], [])
                elif skey[0] == "u":
                    ref = self.dst._emit(OPX_GET, FLAG_PINNED, block, self._get_undef_place(), [])
                else:
                    ref = self._emit_tree(<int32_t>skey[1], block)
            else:
                ref = self.dst._emit(OPX_GET, FLAG_PINNED, block, self._scalar_place_of(<int32_t>skey), [])
            self.dst._emit(OPX_SET, FLAG_SIDE_EFFECT | FLAG_PINNED | FLAG_STMT_ROOT, block, dplace, [ref])

    # -- planning + build --------------------------------------------------

    def _plan(self):
        cdef Func src = self.src
        cdef int32_t i, op, b, e, d
        for i in range(src.n_instrs):
            op = src.instrs[i].op
            if op == OPX_PHI:
                if <int32_t>self.use_count[i] > 0:
                    self.value_temp[i] = self._new_temp(1)
            elif op == OPX_CONST or op == OPX_UNDEF:
                continue
            elif src.instrs[i].flags & FLAG_STMT_ROOT:
                continue
            elif <bint>self.materialize[i]:
                self.value_temp[i] = self._new_temp(1)
        # Critical-edge splits: an edge into a phi-block whose source has >1
        # distinct successor gets its own split block for the phi copies.
        self.n_out = self.nb
        for b in range(src.n_blocks):
            for e in range(src.blocks[b].edge_start, src.blocks[b].edge_start + src.blocks[b].edge_count):
                d = src.edges[e].dst
                if src.blocks[d].phi_count > 0 and len(<list>self.distinct_succ[b]) > 1:
                    self.edge_split[e] = self.n_out
                    self.split_edge.append(e)
                    self.n_out += 1

    def _first_edge(self, int32_t b, int32_t d):
        cdef int32_t e
        for e in range(self.src.blocks[b].edge_start, self.src.blocks[b].edge_start + self.src.blocks[b].edge_count):
            if self.src.edges[e].dst == d:
                return e
        return -1

    def _edge_cond(self, int32_t e):
        if self.src.edges[e].cond_kind == EDGE_COND_NONE:
            return None
        if self.src.edges[e].cond_is_int:
            return int(self.src.edges[e].cond)
        return float(self.src.edges[e].cond)

    def build(self):
        cdef Func src = self.src
        cdef Func dst = self.dst
        self._analyze()
        self._plan()

        if src.n_consts > 0:
            dst.consts = <double*>malloc(<size_t>src.n_consts * sizeof(double))
            if dst.consts == NULL:
                raise MemoryError()
            memcpy(dst.consts, src.consts, <size_t>src.n_consts * sizeof(double))
        dst.n_consts = src.n_consts
        dst.cap_consts = src.n_consts
        dst._const_intern = dict(src._const_intern)
        dst._block_enum_by_id = dict(src._block_enum_by_id)
        dst.blocks_type = src.blocks_type
        dst.callback = src.callback
        dst._block_map = dict(src._block_map)
        dst.is_ssa = False

        cdef int32_t n_out = self.n_out
        dst.blocks = <BlockInfo*>malloc(<size_t>(n_out if n_out > 0 else 1) * sizeof(BlockInfo))
        if dst.blocks == NULL:
            raise MemoryError()
        dst.n_blocks = n_out
        dst.cap_blocks = n_out
        dst.entry_block = src.entry_block

        cdef int32_t b, i, op, istart, tv, di, e, si, val, pid

        for b in range(self.nb):
            istart = dst.n_instrs
            dst.blocks[b].instr_start = istart
            dst.blocks[b].phi_start = 0
            dst.blocks[b].phi_count = 0
            dst.blocks[b].rpo = b
            dst.blocks[b].idom = -1
            for i in range(src.blocks[b].instr_start, src.blocks[b].instr_start + src.blocks[b].instr_count):
                op = src.instrs[i].op
                if op == OPX_PHI or op == OPX_CONST or op == OPX_UNDEF:
                    continue
                if src.instrs[i].flags & FLAG_STMT_ROOT:
                    if op == OPX_SET:
                        val = self._emit_ref(<int32_t>src.args[src.instrs[i].arg_start], b)
                        pid = self._new_place(src.instrs[i].aux, b)
                        dst._emit(OPX_SET, src.instrs[i].flags, b, pid, [val])
                    else:
                        self._emit_tree_root(i, b)
                elif <bint>self.drop[i]:
                    continue
                elif <bint>self.materialize[i]:
                    val = self._emit_tree(i, b)
                    dst._emit(OPX_SET, FLAG_SIDE_EFFECT | FLAG_PINNED | FLAG_STMT_ROOT, b,
                              self._scalar_place_of(<int32_t>self.value_temp[i]), [val])
            if len(<list>self.distinct_succ[b]) == 1:
                di = <int32_t>(<list>self.distinct_succ[b])[0]
                if src.blocks[di].phi_count > 0:
                    e = self._first_edge(b, di)
                    self._emit_copies(e, b)
            tv = src.blocks[b].test_val
            if tv >= 0:
                dst.blocks[b].test_val = self._emit_ref(tv, b)
            else:
                dst.blocks[b].test_val = -1
            dst.blocks[b].instr_count = dst.n_instrs - istart

        for si in range(len(self.split_edge)):
            b = self.nb + si
            e = <int32_t>self.split_edge[si]
            istart = dst.n_instrs
            dst.blocks[b].instr_start = istart
            dst.blocks[b].phi_start = 0
            dst.blocks[b].phi_count = 0
            dst.blocks[b].rpo = b
            dst.blocks[b].idom = -1
            self._emit_copies(e, b)
            dst.blocks[b].test_val = -1
            dst.blocks[b].instr_count = dst.n_instrs - istart

        for b in range(self.nb):
            dst.blocks[b].edge_start = dst.n_edges
            for e in range(src.blocks[b].edge_start, src.blocks[b].edge_start + src.blocks[b].edge_count):
                if <int32_t>self.edge_split[e] >= 0:
                    dst._push_edge(b, <int32_t>self.edge_split[e], self._edge_cond(e))
                else:
                    dst._push_edge(b, src.edges[e].dst, self._edge_cond(e))
            dst.blocks[b].edge_count = dst.n_edges - dst.blocks[b].edge_start
        for si in range(len(self.split_edge)):
            b = self.nb + si
            e = <int32_t>self.split_edge[si]
            dst.blocks[b].edge_start = dst.n_edges
            dst._push_edge(b, src.edges[e].dst, None)
            dst.blocks[b].edge_count = dst.n_edges - dst.blocks[b].edge_start

        return dst


# --------------------------------------------------------------------------
# Coalescing on the final schedule (7.4.4).
# --------------------------------------------------------------------------

cdef void _coalesce(Func func) except *:
    cdef Liveness L = compute_liveness(func)
    cdef int32_t nw = L.n_words
    cdef int32_t n_temps = func.n_temps
    cdef int32_t ni = func.n_instrs
    cdef Instr* instrs = func.instrs
    cdef uint32_t* args = func.args
    cdef PlaceInfo* places = func.places
    if n_temps == 0:
        return

    cdef uint64_t* scalar_mask = <uint64_t*>calloc(<size_t>(nw if nw > 0 else 1), sizeof(uint64_t))
    cdef uint64_t* adj = <uint64_t*>calloc(<size_t>(n_temps * nw if nw > 0 else 1), sizeof(uint64_t))
    cdef uint64_t* work = <uint64_t*>calloc(<size_t>(nw if nw > 0 else 1), sizeof(uint64_t))
    if scalar_mask == NULL or adj == NULL or work == NULL:
        free(scalar_mask); free(adj); free(work)
        raise MemoryError()

    cdef int32_t i, t, u, w, rs, pid, vid, spid, s
    cdef uint64_t* rl
    try:
        for t in range(n_temps):
            if func.temps[t].size == 1:
                bs_set(scalar_mask, t)

        # Interference: at each OPX_SET's live-out, all scalar temps mutually
        # interfere (the standard def-point interference graph; complete).
        for i in range(ni):
            if instrs[i].op != OPX_SET:
                continue
            if not (instrs[i].flags & FLAG_STMT_ROOT):
                continue
            rs = L.root_slot[i]
            if rs < 0:
                continue
            rl = &L.root_live[rs * nw]
            for w in range(nw):
                work[w] = rl[w] & scalar_mask[w]
            for t in range(n_temps):
                if bs_get(work, t):
                    for w in range(nw):
                        adj[t * nw + w] |= work[w]

        # Copy pairs: SET(scalar t) = GET(scalar s), with t live-out.
        copy_pairs = set()
        for i in range(ni):
            if instrs[i].op != OPX_SET or not (instrs[i].flags & FLAG_STMT_ROOT):
                continue
            pid = instrs[i].aux
            if places[pid].kind != PLACE_TEMP_SCALAR:
                continue
            t = places[pid].block_ref
            vid = <int32_t>args[instrs[i].arg_start]
            if instrs[vid].op != OPX_GET:
                continue
            spid = instrs[vid].aux
            if places[spid].kind != PLACE_TEMP_SCALAR:
                continue
            s = places[spid].block_ref
            if s == t:
                continue
            rs = L.root_slot[i]
            if not bs_get(&L.root_live[rs * nw], t):
                continue
            copy_pairs.add((t, s) if t < s else (s, t))

        if not copy_pairs:
            return

        # Interference as Python sets (small funcs), then union non-interfering
        # copy-related webs (old CopyCoalesce.get_mapping), min-canonical.
        interf = {}
        for t in range(n_temps):
            if not bs_get(scalar_mask, t):
                continue
            iset = set()
            for u in range(n_temps):
                if u != t and bs_get(&adj[t * nw], u):
                    iset.add(u)
            interf[t] = iset

        mapping = {}
        for pair in sorted(copy_pairs):
            target = pair[0]
            source = pair[1]
            if source in interf.get(target, set()):
                continue
            combined_map = mapping.get(target, {target}) | mapping.get(source, {source})
            combined_interf = interf.get(target, set()) | interf.get(source, set())
            for place in combined_map:
                mapping[place] = combined_map
                interf[place] = set(combined_interf)
            for place in combined_interf:
                interf.setdefault(place, set()).update(combined_map)

        canonical = {}
        for place, group in mapping.items():
            if place in canonical:
                continue
            c = min(group)
            for member in group:
                canonical[member] = c

        # Apply: rewrite scalar-temp place block_refs to their canonical temp.
        for pid in range(func.n_places):
            if places[pid].kind == PLACE_TEMP_SCALAR:
                t = places[pid].block_ref
                if t in canonical:
                    places[pid].block_ref = <int32_t>canonical[t]

        # Delete self-copies exposed by coalescing (SET(t) = GET(t)).
        for i in range(ni):
            if instrs[i].op != OPX_SET or not (instrs[i].flags & FLAG_STMT_ROOT):
                continue
            pid = instrs[i].aux
            if places[pid].kind != PLACE_TEMP_SCALAR:
                continue
            vid = <int32_t>args[instrs[i].arg_start]
            if instrs[vid].op != OPX_GET:
                continue
            spid = instrs[vid].aux
            if (places[spid].kind == PLACE_TEMP_SCALAR
                    and places[spid].block_ref == places[pid].block_ref):
                instrs[i].flags &= <uint8_t>(~FLAG_STMT_ROOT)
    finally:
        free(scalar_mask)
        free(adj)
        free(work)


# --------------------------------------------------------------------------
# normalize_switch (7.4.5): arithmetic-progression case normalization. Runs
# strictly AFTER all cleanup. Rewrites in place by appending the (test-a)/b
# instructions (referenced only via test_val, so block slices stay contiguous).
# --------------------------------------------------------------------------

cdef void _normalize_switch(Func func) except *:
    cdef int32_t nb = func.n_blocks
    cdef Edge* edges = func.edges
    cdef int32_t b, e, estart, ecount, tv, coff, cstr, new_tv, cid
    cdef bint has_default
    cdef double offv, strv, cval, newc
    cdef list cases
    for b in range(nb):
        estart = func.blocks[b].edge_start
        ecount = func.blocks[b].edge_count
        cases = []
        has_default = False
        for e in range(estart, estart + ecount):
            if edges[e].cond_kind == EDGE_COND_NONE:
                has_default = True
            else:
                cases.append(edges[e].cond)
        cases = sorted(set(cases))
        # Mirror old NormalizeSwitch: at least 3 distinct outgoing conds (numeric
        # cases + default). Fewer (two-way / default-less 2-case) is not a switch.
        if len(cases) + (1 if has_default else 0) < 3:
            continue
        res = _offset_stride(cases)
        if res is None:
            continue
        offv = <double>res[0]
        strv = <double>res[1]
        if offv == 0.0 and strv == 1.0:
            continue
        # Rewrite case conds to 0..k-1.
        for e in range(estart, estart + ecount):
            if edges[e].cond_kind == EDGE_COND_VALUE:
                newc = <double>(<int32_t>((edges[e].cond - offv) / strv))
                edges[e].cond = newc
                edges[e].cond_is_int = 1
        # Rewrite the test to (test - a) / b, appended at the arena end (block=b,
        # referenced via test_val; not a statement root, not in the block slice).
        tv = func.blocks[b].test_val
        new_tv = tv
        if offv != 0.0:
            cid = func._intern_const(offv)
            coff = func._emit(OPX_CONST, FLAG_PURE | FLAG_CONST_IS_INT, b, cid, [])
            new_tv = func._emit(<uint16_t>OP_Subtract, FLAG_PURE, b, -1, [new_tv, coff])
        if strv != 1.0:
            cid = func._intern_const(strv)
            cstr = func._emit(OPX_CONST, FLAG_PURE | FLAG_CONST_IS_INT, b, cid, [])
            new_tv = func._emit(<uint16_t>OP_Divide, FLAG_PURE, b, -1, [new_tv, cstr])
        func.blocks[b].test_val = new_tv


def _offset_stride(list cases):
    # cases: sorted distinct case values (as f64). Return (offset, stride) ints,
    # or None. Mirrors old NormalizeSwitch.get_offset_stride.
    if len(cases) < 2:
        return None
    cdef double offset = <double>cases[0]
    cdef double stride = <double>cases[1] - offset
    if offset != float(int(offset)) or stride != float(int(stride)):
        return None
    if stride == 0.0:
        return None
    cdef int32_t i
    cdef double case
    for i in range(2, len(cases)):
        case = <double>cases[i]
        if case != offset + i * stride:
            return None
    return (int(offset), int(stride))


# --------------------------------------------------------------------------
# Driver: lower_from_ssa (7.4, steps 1-5).
# --------------------------------------------------------------------------

cdef Func lower_from_ssa(Func func):
    """Lower an SSA ``Func`` to a non-SSA 3-legal arena (treeify + out-of-SSA).

    Returns a fresh ``Func``: scheduling decision + phi elimination + tree
    emission (``_Lower``), then final-schedule coalescing, phi-free cfg_cleanup
    (RPO layout), and normalize_switch. Ready for ``allocate_func`` + emission.
    """
    cdef _Lower lo = _Lower(func)
    cdef Func lowered = <Func>lo.build()
    _coalesce(lowered)
    cdef Func cleaned = cfg_cleanup(lowered, True)
    _normalize_switch(cleaned)
    return cleaned


# --------------------------------------------------------------------------
# Python-visible test/driver wrappers.
# --------------------------------------------------------------------------

def run_lower(entry, mode=None, callback=None, strategy="packing", midend=False):
    """marshal -> cfg_cleanup -> build_ssa -> [midend] -> lower_from_ssa ->
    allocate -> export.

    The ``midend`` flag optionally runs a mid-end round if the parallel mid-end
    agent has exposed one (probed by name; skipped otherwise -- lowering does not
    depend on it). ``strategy`` is the allocator ("packing"/"bump"/"try_bump").
    """
    cdef Func func = cfg_cleanup(<Func>marshal_in(entry, mode, callback), False)
    cdef Func ssa = build_ssa(func)
    ssa = _maybe_midend(ssa, midend)
    cdef Func lowered = lower_from_ssa(ssa)
    cdef int32_t code = _strategy_code(strategy)
    allocate_func(lowered, code)
    return to_basic_blocks(lowered)


def lower_debug(entry, mode=None, callback=None, midend=False):
    """Pre-allocation lowering form (the ``allocate=False`` equivalent).

    marshal -> cfg_cleanup -> build_ssa -> [midend] -> lower_from_ssa -> export,
    leaving temp places unallocated (for inspection / node-count comparison).
    """
    cdef Func func = cfg_cleanup(<Func>marshal_in(entry, mode, callback), False)
    cdef Func ssa = build_ssa(func)
    ssa = _maybe_midend(ssa, midend)
    cdef Func lowered = lower_from_ssa(ssa)
    return to_basic_blocks(lowered)


cdef Func _maybe_midend(Func ssa, bint midend):
    # Optionally run a mid-end round if the parallel agent exposed a Python-level
    # ``midend_round`` on the midend module. Do NOT depend on it existing.
    if not midend:
        return ssa
    from sonolus.backend._opt import midend as _m
    fn = getattr(_m, "midend_round", None)
    if fn is None:
        return ssa
    return <Func>fn(ssa)
