# cython: language_level=3
"""Mid-end pass: ``cfg_cleanup`` (milestone M1).

Worklist-based CFG cleanup over the arena IR, subsuming the old Python passes
``CoalesceFlow`` + ``UnreachableCodeElimination`` + ``CombineExitBlocks`` +
``CoalesceSmallConditionalBlocks`` (see OPTIMIZER_REWRITE.md section 7.1 and the
originals under ``sonolus/backend/optimize/{simplify,dead_code}.py``).

Design
------
The input arena (fresh from ``marshal_in``) is a *forest*: marshal-in does no
value CSE, so every block's instruction slice is a set of independent statement
and test trees with no shared sub-values. That makes the pass structure-only:
it never rewrites instructions, it reorganises the *block/edge graph* and records
which source blocks' statement trees feed each output block (a "chain").

A logical graph is built over the source block ids:

* ``alive``/``is_head`` -- a live output block is a "head"; a source block merged
  into another head (single-pred/succ chain merge) is killed.
* per head, a *chain* of source block ids (linked-list node pool): the output
  block's statements are the concatenation of the chain members' statements and
  its test/edges come from the chain *tail*.
* a flat array of logical edges ``(src_head, dst_head, cond)``; parallel edges
  between a head pair are legal (matching section 3).

Transformations run to a fixpoint (each is monotone or bounded, so the pass
terminates and is deterministic -- everything iterates ids in order, no set
iteration):

* constant-test fold (finalize edge-selection semantics),
* parallel-edge dedup (dict-keyed-by-cond model),
* empty-block threading (forwarder elimination),
* single-pred/single-succ chain merge,
* bounded tail-duplication of <=1-statement blocks into single-uncond preds
  (pre-SSA only; guarded by ``phi_safe`` and a per-(head,target) firing cap that
  guarantees termination),
* unreachable elimination,
* shared-empty-exit canonicalization.

The cleaned logical graph is then rebuilt into a fresh ``Func`` arena by copying
each output block's statement/test trees out of the source arena (temps/consts
carried over 1:1, so identity is preserved), which keeps ``verify()`` green and
lets ``to_basic_blocks`` export it unchanged.
"""

from libc.stdint cimport int16_t, int32_t, uint8_t, uint16_t, uint32_t
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
    PlaceInfo,
    PLACE_DYNAMIC_BLOCK,
    PLACE_REAL_BLOCK,
    PLACE_TEMP_ARRAY,
    PLACE_TEMP_SCALAR,
    PLACE_TEMP_SIZE0,
    PLACE_WRITABLE,
    TempInfo,
)
from sonolus.backend._opt._ops_gen cimport OPX_CONST, OPX_GET, OPX_PHI, OPX_SET, OPX_UNDEF
from sonolus.backend._opt.analysis cimport compute_dominators

from sonolus.backend._opt.ir import marshal_in, to_basic_blocks


# --------------------------------------------------------------------------
# growable-buffer helper for the places array (single array, own capacity)
# --------------------------------------------------------------------------

cdef PlaceInfo* _realloc_places(PlaceInfo* buf, int32_t* cap, int32_t need) except NULL:
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


cdef class _Cleaner:
    """Logical-graph CFG-cleanup working state over one source ``Func``."""

    cdef Func src
    cdef bint phi_safe
    cdef int32_t nb                 # source block count

    cdef uint8_t* alive             # [nb]  block still live
    cdef uint8_t* is_head           # [nb]  block is a chain head (an output block)
    cdef int32_t* nstmt             # [nb]  statement (FLAG_STMT_ROOT) count per source block

    # chain-node pool: node k -> (source block cn_src[k], next node cn_next[k]).
    cdef int32_t* cn_src
    cdef int32_t* cn_next
    cdef int32_t n_cn
    cdef int32_t cap_cn
    cdef int32_t* chain_head        # [nb]  first chain node of a head
    cdef int32_t* chain_tail        # [nb]  last chain node of a head

    # flat logical-edge arrays.
    cdef int32_t* e_src
    cdef int32_t* e_dst
    cdef uint8_t* e_ck              # EDGE_COND_NONE / EDGE_COND_VALUE
    cdef uint8_t* e_ci              # cond-is-int display bit
    cdef double* e_cond
    cdef uint8_t* e_alive
    cdef int32_t n_e
    cdef int32_t cap_e

    cdef int32_t entry_head
    cdef uint8_t* tailduped         # [nb*nb]  (head,target) pair already tail-duped

    # ---- construction ------------------------------------------------------

    def __cinit__(self, Func src, bint phi_safe):
        self.src = src
        self.phi_safe = phi_safe
        self.nb = src.n_blocks
        self.alive = NULL
        self.is_head = NULL
        self.nstmt = NULL
        self.cn_src = NULL
        self.cn_next = NULL
        self.n_cn = 0
        self.cap_cn = 0
        self.chain_head = NULL
        self.chain_tail = NULL
        self.e_src = NULL
        self.e_dst = NULL
        self.e_ck = NULL
        self.e_ci = NULL
        self.e_cond = NULL
        self.e_alive = NULL
        self.n_e = 0
        self.cap_e = 0
        self.entry_head = src.entry_block
        self.tailduped = NULL
        self._build()

    def __dealloc__(self):
        free(self.alive)
        free(self.is_head)
        free(self.nstmt)
        free(self.cn_src)
        free(self.cn_next)
        free(self.chain_head)
        free(self.chain_tail)
        free(self.e_src)
        free(self.e_dst)
        free(self.e_ck)
        free(self.e_ci)
        free(self.e_cond)
        free(self.e_alive)
        free(self.tailduped)

    cdef void _ensure_edge_cap(self, int32_t need) except *:
        # the six edge arrays share cap_e; grow them all in lockstep.
        if need <= self.cap_e:
            return
        cdef int32_t nc = self.cap_e if self.cap_e > 0 else 8
        while nc < need:
            nc *= 2
        self.e_src = <int32_t*>realloc(self.e_src, <size_t>nc * sizeof(int32_t))
        self.e_dst = <int32_t*>realloc(self.e_dst, <size_t>nc * sizeof(int32_t))
        self.e_ck = <uint8_t*>realloc(self.e_ck, <size_t>nc * sizeof(uint8_t))
        self.e_ci = <uint8_t*>realloc(self.e_ci, <size_t>nc * sizeof(uint8_t))
        self.e_cond = <double*>realloc(self.e_cond, <size_t>nc * sizeof(double))
        self.e_alive = <uint8_t*>realloc(self.e_alive, <size_t>nc * sizeof(uint8_t))
        if (self.e_src == NULL or self.e_dst == NULL or self.e_ck == NULL
                or self.e_ci == NULL or self.e_cond == NULL or self.e_alive == NULL):
            raise MemoryError()
        self.cap_e = nc

    cdef void _add_edge(self, int32_t s, int32_t d, uint8_t ck, uint8_t ci, double cond) except *:
        cdef int32_t k = self.n_e
        self._ensure_edge_cap(k + 1)
        self.e_src[k] = s
        self.e_dst[k] = d
        self.e_ck[k] = ck
        self.e_ci[k] = ci
        self.e_cond[k] = cond
        self.e_alive[k] = 1
        self.n_e = k + 1

    cdef int32_t _new_cn(self, int32_t src_block) except -1:
        cdef int32_t k = self.n_cn
        cdef int32_t nc
        if k + 1 > self.cap_cn:
            nc = self.cap_cn if self.cap_cn > 0 else 8
            while nc < k + 1:
                nc *= 2
            self.cn_src = <int32_t*>realloc(self.cn_src, <size_t>nc * sizeof(int32_t))
            self.cn_next = <int32_t*>realloc(self.cn_next, <size_t>nc * sizeof(int32_t))
            if self.cn_src == NULL or self.cn_next == NULL:
                raise MemoryError()
            self.cap_cn = nc
        self.cn_src[k] = src_block
        self.cn_next[k] = -1
        self.n_cn = k + 1
        return k

    cdef void _build(self) except *:
        cdef Func src = self.src
        cdef int32_t nb = self.nb
        cdef int32_t b, i, node

        self.alive = <uint8_t*>calloc(nb, sizeof(uint8_t))
        self.is_head = <uint8_t*>calloc(nb, sizeof(uint8_t))
        self.nstmt = <int32_t*>calloc(nb, sizeof(int32_t))
        self.chain_head = <int32_t*>malloc(<size_t>nb * sizeof(int32_t))
        self.chain_tail = <int32_t*>malloc(<size_t>nb * sizeof(int32_t))
        self.tailduped = <uint8_t*>calloc(<size_t>nb * <size_t>nb, sizeof(uint8_t))
        if (self.alive == NULL or self.is_head == NULL or self.nstmt == NULL
                or self.chain_head == NULL or self.chain_tail == NULL or self.tailduped == NULL):
            raise MemoryError()

        # statement counts + one-element chain per block.
        for b in range(nb):
            self.nstmt[b] = 0
            for i in range(src.blocks[b].instr_start, src.blocks[b].instr_start + src.blocks[b].instr_count):
                if src.instrs[i].flags & FLAG_STMT_ROOT:
                    self.nstmt[b] += 1
            node = self._new_cn(b)
            self.chain_head[b] = node
            self.chain_tail[b] = node

        # reachability from entry over source edges.
        cdef int32_t* stack = <int32_t*>malloc(<size_t>nb * sizeof(int32_t))
        if stack == NULL:
            raise MemoryError()
        cdef int32_t sp = 0
        cdef int32_t cur, d
        self.alive[self.entry_head] = 1
        stack[sp] = self.entry_head
        sp += 1
        while sp > 0:
            sp -= 1
            cur = stack[sp]
            for i in range(src.blocks[cur].edge_start, src.blocks[cur].edge_start + src.blocks[cur].edge_count):
                d = src.edges[i].dst
                if not self.alive[d]:
                    self.alive[d] = 1
                    stack[sp] = d
                    sp += 1
        free(stack)

        for b in range(nb):
            self.is_head[b] = self.alive[b]

        # copy source edges between reachable blocks into the logical-edge array.
        for i in range(src.n_edges):
            b = src.edges[i].src
            d = src.edges[i].dst
            if self.alive[b] and self.alive[d]:
                self._add_edge(b, d, src.edges[i].cond_kind, src.edges[i].cond_is_int, src.edges[i].cond)

    # ---- chain helpers -----------------------------------------------------

    cdef int32_t _chain_nstmt(self, int32_t head):
        cdef int32_t total = 0
        cdef int32_t node = self.chain_head[head]
        while node != -1:
            total += self.nstmt[self.cn_src[node]]
            node = self.cn_next[node]
        return total

    cdef void _append_chain_transfer(self, int32_t a, int32_t b) except *:
        # Splice b's chain onto a (b is about to be killed; reuse its nodes).
        self.cn_next[self.chain_tail[a]] = self.chain_head[b]
        self.chain_tail[a] = self.chain_tail[b]

    cdef void _append_chain_copy(self, int32_t a, int32_t b) except *:
        # Append a fresh copy of b's chain onto a (b stays live -- tail-dup).
        cdef int32_t node = self.chain_head[b]
        cdef int32_t nn
        while node != -1:
            nn = self._new_cn(self.cn_src[node])
            self.cn_next[self.chain_tail[a]] = nn
            self.chain_tail[a] = nn
            node = self.cn_next[node]

    # ---- edge helpers ------------------------------------------------------

    cdef bint _has_value_edge(self, int32_t h):
        cdef int32_t i
        for i in range(self.n_e):
            if self.e_alive[i] and self.e_src[i] == h and self.e_ck[i] == EDGE_COND_VALUE:
                return True
        return False

    cdef int32_t _single_none_succ(self, int32_t h, int32_t* edge_out):
        # If h has exactly one alive out-edge and it is unconditional, return the
        # target head and set *edge_out to the edge index; else return -1.
        cdef int32_t i
        cdef int32_t cnt = 0
        cdef int32_t the = -1
        for i in range(self.n_e):
            if self.e_alive[i] and self.e_src[i] == h:
                cnt += 1
                the = i
        if cnt != 1 or self.e_ck[the] != EDGE_COND_NONE:
            return -1
        edge_out[0] = the
        return self.e_dst[the]

    # ---- transformations ---------------------------------------------------

    cdef bint _const_fold(self) except -1:
        cdef bint changed = False
        cdef int32_t h, i, tv, tail_block, live_edge
        cdef double tval
        for h in range(self.nb):
            if not self.alive[h] or not self.is_head[h]:
                continue
            if not self._has_value_edge(h):
                continue
            tail_block = self.cn_src[self.chain_tail[h]]
            tv = self.src.blocks[tail_block].test_val
            if tv < 0 or self.src.instrs[tv].op != OPX_CONST:
                continue
            tval = self.src.consts[self.src.instrs[tv].aux]
            # finalize edge-selection: value edge whose cond == test, else the
            # unconditional (default) edge, else the block becomes an exit.
            live_edge = -1
            for i in range(self.n_e):
                if self.e_alive[i] and self.e_src[i] == h and self.e_ck[i] == EDGE_COND_VALUE:
                    if self.e_cond[i] == tval:  # C ==: NaN matches nothing; -0.0 == 0.0
                        live_edge = i
                        break
            if live_edge == -1:
                for i in range(self.n_e):
                    if self.e_alive[i] and self.e_src[i] == h and self.e_ck[i] == EDGE_COND_NONE:
                        live_edge = i
                        break
            for i in range(self.n_e):
                if self.e_alive[i] and self.e_src[i] == h and i != live_edge:
                    self.e_alive[i] = 0
                    changed = True
            if live_edge != -1 and self.e_ck[live_edge] != EDGE_COND_NONE:
                self.e_ck[live_edge] = EDGE_COND_NONE
                self.e_ci[live_edge] = 0
                self.e_cond[live_edge] = 0.0
                changed = True
        return changed

    cdef bint _dedup(self) except -1:
        cdef bint changed = False
        cdef int32_t h, i, j, default_dst
        for h in range(self.nb):
            if not self.alive[h] or not self.is_head[h]:
                continue
            # value edge to the same target as the default edge is redundant.
            default_dst = -1
            for i in range(self.n_e):
                if self.e_alive[i] and self.e_src[i] == h and self.e_ck[i] == EDGE_COND_NONE:
                    default_dst = self.e_dst[i]
                    break
            if default_dst != -1:
                for i in range(self.n_e):
                    if (self.e_alive[i] and self.e_src[i] == h
                            and self.e_ck[i] == EDGE_COND_VALUE and self.e_dst[i] == default_dst):
                        self.e_alive[i] = 0
                        changed = True
            # duplicate edges keyed by cond (the outgoing dict collapses these).
            for i in range(self.n_e):
                if not (self.e_alive[i] and self.e_src[i] == h):
                    continue
                for j in range(i + 1, self.n_e):
                    if not (self.e_alive[j] and self.e_src[j] == h):
                        continue
                    if self.e_ck[i] != self.e_ck[j]:
                        continue
                    if self.e_ck[i] == EDGE_COND_NONE or self.e_cond[i] == self.e_cond[j]:
                        self.e_alive[j] = 0
                        changed = True
        return changed

    cdef bint _thread_forwarders(self) except -1:
        cdef bint changed = False
        cdef int32_t h, i, the_edge, c
        for h in range(self.nb):
            if not self.alive[h] or not self.is_head[h]:
                continue
            if self._chain_nstmt(h) != 0:
                continue
            c = self._single_none_succ(h, &the_edge)
            if c == -1 or c == h:
                continue
            # h is a pure forwarder: redirect its predecessors straight to c.
            for i in range(self.n_e):
                if self.e_alive[i] and self.e_dst[i] == h:
                    self.e_dst[i] = c
            if h == self.entry_head:
                self.entry_head = c
            self.e_alive[the_edge] = 0
            self.alive[h] = 0
            self.is_head[h] = 0
            changed = True
        return changed

    cdef bint _merge_single(self) except -1:
        cdef bint changed = False
        cdef int32_t h, i, the_edge, c, incnt
        for h in range(self.nb):
            if not self.alive[h] or not self.is_head[h]:
                continue
            c = self._single_none_succ(h, &the_edge)
            if c == -1 or c == h or c == self.entry_head:
                continue
            incnt = 0
            for i in range(self.n_e):
                if self.e_alive[i] and self.e_dst[i] == c:
                    incnt += 1
            if incnt != 1:
                continue
            # c has h as its only predecessor: absorb c into h.
            self.e_alive[the_edge] = 0
            self._append_chain_transfer(h, c)
            for i in range(self.n_e):
                if self.e_alive[i] and self.e_src[i] == c:
                    self.e_src[i] = h
            self.alive[c] = 0
            self.is_head[c] = 0
            changed = True
        return changed

    cdef bint _taildup(self) except -1:
        cdef bint changed = False
        cdef int32_t h, i, the_edge, c, n_e0
        if self.phi_safe:
            return False
        for h in range(self.nb):
            if not self.alive[h] or not self.is_head[h]:
                continue
            c = self._single_none_succ(h, &the_edge)
            if c == -1 or c == h or c == self.entry_head:
                continue
            if self._chain_nstmt(c) > 1:
                continue
            if self.tailduped[h * self.nb + c]:
                continue
            # duplicate the tiny block c into h (h unconditionally reaches c), then
            # branch like c -- exposing threading. Bounded once per (h, c) pair.
            self.tailduped[h * self.nb + c] = 1
            self.e_alive[the_edge] = 0
            self._append_chain_copy(h, c)
            n_e0 = self.n_e
            for i in range(n_e0):
                if self.e_alive[i] and self.e_src[i] == c:
                    self._add_edge(h, self.e_dst[i], self.e_ck[i], self.e_ci[i], self.e_cond[i])
            changed = True
        return changed

    cdef bint _prune_unreachable(self) except -1:
        cdef bint changed = False
        cdef int32_t i, cur, d, sp
        cdef uint8_t* visited = <uint8_t*>calloc(self.nb, sizeof(uint8_t))
        cdef int32_t* stack = <int32_t*>malloc(<size_t>self.nb * sizeof(int32_t))
        if visited == NULL or stack == NULL:
            free(visited)
            free(stack)
            raise MemoryError()
        sp = 0
        visited[self.entry_head] = 1
        stack[sp] = self.entry_head
        sp += 1
        while sp > 0:
            sp -= 1
            cur = stack[sp]
            for i in range(self.n_e):
                if self.e_alive[i] and self.e_src[i] == cur:
                    d = self.e_dst[i]
                    if not visited[d]:
                        visited[d] = 1
                        stack[sp] = d
                        sp += 1
        for i in range(self.nb):
            if self.alive[i] and self.is_head[i] and not visited[i]:
                self.alive[i] = 0
                self.is_head[i] = 0
                changed = True
        for i in range(self.n_e):
            if self.e_alive[i] and (not visited[self.e_src[i]] or not visited[self.e_dst[i]]):
                self.e_alive[i] = 0
                changed = True
        free(visited)
        free(stack)
        return changed

    cdef bint _canonicalize_exits(self) except -1:
        # Merge all statement-less no-outgoing (empty exit) blocks into one shared
        # exit; blocks with statements ending in exit keep their statements.
        cdef bint changed = False
        cdef list order = self._rpo_order()
        cdef int32_t first_exit = -1
        cdef int32_t h, i
        cdef bint has_out
        for h in order:
            if not self.alive[h] or not self.is_head[h]:
                continue
            has_out = False
            for i in range(self.n_e):
                if self.e_alive[i] and self.e_src[i] == h:
                    has_out = True
                    break
            if has_out or self._chain_nstmt(h) != 0:
                continue
            if first_exit == -1:
                first_exit = h
            else:
                for i in range(self.n_e):
                    if self.e_alive[i] and self.e_dst[i] == h:
                        self.e_dst[i] = first_exit
                self.alive[h] = 0
                self.is_head[h] = 0
                changed = True
        return changed

    # ---- ordering ----------------------------------------------------------

    cdef list _sorted_succ(self, int32_t h):
        # dst heads of h's alive edges, sorted (unconditional last, then by cond).
        cdef list items = []
        cdef int32_t i
        for i in range(self.n_e):
            if self.e_alive[i] and self.e_src[i] == h:
                if self.e_ck[i] == EDGE_COND_NONE:
                    items.append((1, 0.0, self.e_dst[i]))
                else:
                    items.append((0, self.e_cond[i], self.e_dst[i]))
        items.sort()
        return [it[2] for it in items]

    cdef void _dfs_post(self, int32_t b, uint8_t* visited, list post) except *:
        visited[b] = 1
        cdef int32_t d
        for d in self._sorted_succ(b):
            if not visited[d]:
                self._dfs_post(d, visited, post)
        post.append(b)

    cdef list _rpo_order(self):
        cdef uint8_t* visited = <uint8_t*>calloc(self.nb, sizeof(uint8_t))
        if visited == NULL:
            raise MemoryError()
        cdef list post = []
        try:
            self._dfs_post(self.entry_head, visited, post)
        finally:
            free(visited)
        post.reverse()
        return post

    # ---- driver ------------------------------------------------------------

    cdef void run(self) except *:
        cdef bint changed = True
        cdef long iters = 0
        cdef long cap = <long>self.nb * self.nb + self.n_e + 100000
        while changed:
            changed = False
            if self._const_fold():
                changed = True
            if self._dedup():
                changed = True
            if self._thread_forwarders():
                changed = True
            if self._merge_single():
                changed = True
            if self._taildup():
                changed = True
            if self._prune_unreachable():
                changed = True
            if self._canonicalize_exits():
                changed = True
            iters += 1
            if iters > cap:
                raise RuntimeError("cfg_cleanup did not converge")

    # ---- rebuild -----------------------------------------------------------

    cdef int32_t _dst_add_place(self, Func dst, uint8_t kind, uint8_t flags,
                                int32_t block_ref, int32_t index_val, int32_t offset) except -1:
        cdef int32_t pid = dst.n_places
        dst.places = <PlaceInfo*>_realloc_places(dst.places, &dst.cap_places, pid + 1)
        dst.places[pid].kind = kind
        dst.places[pid].flags = flags
        dst.places[pid].block_ref = block_ref
        dst.places[pid].index_val = index_val
        dst.places[pid].offset = offset
        dst.n_places = pid + 1
        return pid

    cdef int32_t _copy_place(self, Func dst, int32_t pid, int32_t k) except -1:
        cdef PlaceInfo* p = &self.src.places[pid]
        cdef int32_t new_block_ref = p.block_ref
        cdef int32_t new_index_val = -1
        if p.kind == PLACE_DYNAMIC_BLOCK:
            new_block_ref = self._copy_value(dst, p.block_ref, k)
        if p.index_val >= 0:
            new_index_val = self._copy_value(dst, p.index_val, k)
        return self._dst_add_place(dst, p.kind, p.flags, new_block_ref, new_index_val, p.offset)

    cdef int32_t _copy_value(self, Func dst, int32_t vid, int32_t k) except -1:
        cdef Instr* ins = &self.src.instrs[vid]
        cdef uint16_t op = ins.op
        cdef uint8_t flags = ins.flags
        cdef int32_t pid, j
        cdef list new_args
        if op == OPX_CONST:
            return dst._emit(OPX_CONST, flags, k, ins.aux, [])
        if op == OPX_GET:
            pid = self._copy_place(dst, ins.aux, k)
            return dst._emit(OPX_GET, flags, k, pid, [])
        if op == OPX_SET:
            raise AssertionError("OPX_SET in value position")
        new_args = [self._copy_value(dst, <int32_t>self.src.args[ins.arg_start + j], k)
                    for j in range(ins.nargs)]
        return dst._emit(op, flags, k, -1, new_args)

    cdef void _copy_stmt(self, Func dst, int32_t i, int32_t k) except *:
        cdef Instr* ins = &self.src.instrs[i]
        cdef int32_t val, pid
        if ins.op == OPX_SET:
            val = self._copy_value(dst, <int32_t>self.src.args[ins.arg_start], k)
            pid = self._copy_place(dst, ins.aux, k)
            dst._emit(OPX_SET, ins.flags, k, pid, [val])
        else:
            self._copy_value(dst, i, k)

    cdef Func rebuild(self):
        cdef Func src = self.src
        cdef Func dst = Func()
        cdef list order = self._rpo_order()
        cdef int32_t nb_out = len(order)
        cdef int32_t k, h, node, sb, i, tail_block
        cdef object pycond

        # blocks array.
        dst.blocks = <BlockInfo*>malloc(<size_t>nb_out * sizeof(BlockInfo))
        if dst.blocks == NULL:
            raise MemoryError()
        dst.n_blocks = nb_out
        dst.cap_blocks = nb_out
        dst.entry_block = 0

        # consts and temps carry over 1:1 so const/temp ids stay valid on copy.
        if src.n_consts > 0:
            dst.consts = <double*>malloc(<size_t>src.n_consts * sizeof(double))
            if dst.consts == NULL:
                raise MemoryError()
            memcpy(dst.consts, src.consts, <size_t>src.n_consts * sizeof(double))
        dst.n_consts = src.n_consts
        dst.cap_consts = src.n_consts
        dst._const_intern = dict(src._const_intern)

        if src.n_temps > 0:
            dst.temps = <TempInfo*>malloc(<size_t>src.n_temps * sizeof(TempInfo))
            if dst.temps == NULL:
                raise MemoryError()
            memcpy(dst.temps, src.temps, <size_t>src.n_temps * sizeof(TempInfo))
        dst.n_temps = src.n_temps
        dst.cap_temps = src.n_temps
        dst.names = list(src.names)
        dst._temp_intern = dict(src._temp_intern)

        # boundary metadata needed by the exporter (enum block reproduction).
        dst._block_enum_by_id = dict(src._block_enum_by_id)
        dst.blocks_type = src.blocks_type
        dst.callback = src.callback
        dst._block_map = dict(src._block_map)

        cdef int32_t* head2idx = <int32_t*>malloc(<size_t>self.nb * sizeof(int32_t))
        if head2idx == NULL:
            raise MemoryError()
        for i in range(self.nb):
            head2idx[i] = -1
        for k in range(nb_out):
            head2idx[<int32_t>order[k]] = k

        for k in range(nb_out):
            h = <int32_t>order[k]
            dst.blocks[k].instr_start = dst.n_instrs
            dst.blocks[k].phi_start = 0
            dst.blocks[k].phi_count = 0
            dst.blocks[k].rpo = k
            dst.blocks[k].idom = -1
            # statements: all chain members, in order.
            node = self.chain_head[h]
            while node != -1:
                sb = self.cn_src[node]
                for i in range(src.blocks[sb].instr_start, src.blocks[sb].instr_start + src.blocks[sb].instr_count):
                    if src.instrs[i].flags & FLAG_STMT_ROOT:
                        self._copy_stmt(dst, i, k)
                node = self.cn_next[node]
            # test: only where actually used (block has a case edge).
            if self._has_value_edge(h):
                tail_block = self.cn_src[self.chain_tail[h]]
                if src.blocks[tail_block].test_val >= 0:
                    dst.blocks[k].test_val = self._copy_value(dst, src.blocks[tail_block].test_val, k)
                else:
                    dst.blocks[k].test_val = -1
            else:
                dst.blocks[k].test_val = -1
            dst.blocks[k].instr_count = dst.n_instrs - dst.blocks[k].instr_start
            # edges, sorted deterministically (unconditional last, then by cond).
            dst.blocks[k].edge_start = dst.n_edges
            for edge_idx in self._sorted_edge_indices(h):
                i = <int32_t>edge_idx
                if self.e_ck[i] == EDGE_COND_NONE:
                    pycond = None
                elif self.e_ci[i]:
                    pycond = int(self.e_cond[i])
                else:
                    pycond = float(self.e_cond[i])
                dst._push_edge(k, head2idx[self.e_dst[i]], pycond)
            dst.blocks[k].edge_count = dst.n_edges - dst.blocks[k].edge_start

        free(head2idx)
        return dst

    cdef list _sorted_edge_indices(self, int32_t h):
        cdef list items = []
        cdef int32_t i
        for i in range(self.n_e):
            if self.e_alive[i] and self.e_src[i] == h:
                if self.e_ck[i] == EDGE_COND_NONE:
                    items.append((1, 0.0, i))
                else:
                    items.append((0, self.e_cond[i], i))
        items.sort()
        return [it[2] for it in items]


cdef Func cfg_cleanup(Func func, bint phi_safe):
    """Run CFG cleanup over ``func``; returns a fresh cleaned arena ``Func``."""
    cdef _Cleaner cleaner = _Cleaner(func, phi_safe)
    cleaner.run()
    return cleaner.rebuild()


def cleanup_func(entry, mode=None, callback=None, phi_safe=False):
    """marshal_in -> cfg_cleanup -> cleaned arena ``Func`` (verify()-able)."""
    cdef Func func = marshal_in(entry, mode, callback)
    return cfg_cleanup(func, phi_safe)


def run_cfg_cleanup(entry, mode=None, callback=None, phi_safe=False):
    """marshal_in -> cfg_cleanup -> to_basic_blocks (test/debug entry point)."""
    return to_basic_blocks(cleanup_func(entry, mode, callback, phi_safe))


# ==========================================================================
# SSA construction -- Braun et al. (CC'13) on-the-fly + trivial-phi removal.
#
# Only size-1 scalar temps promote: their OPX_GET/OPX_SET dissolve into value
# edges (readVariable / writeVariable). Arrays, size-0, real-block, and
# dynamic-block accesses stay as pinned OPX_GET/OPX_SET memory ops. A read of a
# never-written scalar yields the shared OPX_UNDEF value (a REACHABLE case, e.g.
# a provably-dead VarArray[Num,1] access). Phis are OPX_PHI instructions with one
# operand PER INCOMING EDGE, in the incoming-edge contract order (see ir.pxd).
#
# Construction runs over Python-level working state (GIL held; correctness-first)
# then compacts into a fresh tight arena ``Func`` with contiguous per-block instr
# slices (phis first) so the M2+ nogil passes get the flat value-based form.
# ==========================================================================

import sys as _sys


cdef class _SSABuilder:
    cdef Func src
    cdef int32_t nb
    cdef int32_t entry
    # loose value store (index == loose value-id)
    cdef list val_op
    cdef list val_flags
    cdef list val_block
    cdef list val_aux
    cdef list val_args      # list[list[int]] (per-edge operands for phis)
    cdef list val_dead
    cdef dict subst         # eliminated phi -> replacement value
    cdef set undef_widened  # values used out of dominance region via phi(UNDEF,v)=v
    cdef dict phi_users     # value -> set of phi values referencing it
    cdef dict cur_def       # (temp, block) -> value
    cdef list sealed
    cdef list filled
    cdef list incomplete    # per block: dict temp -> phi value
    cdef list block_phis    # per block: list of phi values (creation order)
    cdef list block_values  # per block: list of non-phi values (creation order)
    cdef list block_test    # per block: value or -1
    cdef int32_t undef_val
    cdef list incoming      # per block: list of edge indices (ascending == contract)
    cdef list succs_distinct
    cdef list unfilled_preds
    # loose SSA place table
    cdef list sp_kind
    cdef list sp_flags
    cdef list sp_block_ref
    cdef list sp_index_val
    cdef list sp_offset
    cdef dict sp_intern

    def __cinit__(self, Func src):
        self.src = src
        self.nb = src.n_blocks
        self.entry = src.entry_block
        self.val_op = []
        self.val_flags = []
        self.val_block = []
        self.val_aux = []
        self.val_args = []
        self.val_dead = []
        self.subst = {}
        self.undef_widened = set()
        self.phi_users = {}
        self.cur_def = {}
        self.sealed = [False] * self.nb
        self.filled = [False] * self.nb
        self.incomplete = [dict() for _ in range(self.nb)]
        self.block_phis = [[] for _ in range(self.nb)]
        self.block_values = [[] for _ in range(self.nb)]
        self.block_test = [-1] * self.nb
        self.undef_val = -1
        self.sp_kind = []
        self.sp_flags = []
        self.sp_block_ref = []
        self.sp_index_val = []
        self.sp_offset = []
        self.sp_intern = {}
        # CFG topology from the source arena.
        cdef int32_t e, b
        self.incoming = [[] for _ in range(self.nb)]
        for e in range(src.n_edges):
            self.incoming[src.edges[e].dst].append(e)
        cdef list succ_sets = [set() for _ in range(self.nb)]
        cdef list pred_sets = [set() for _ in range(self.nb)]
        for e in range(src.n_edges):
            succ_sets[src.edges[e].src].add(src.edges[e].dst)
            pred_sets[src.edges[e].dst].add(src.edges[e].src)
        self.succs_distinct = [sorted(s) for s in succ_sets]
        self.unfilled_preds = [len(pred_sets[b]) for b in range(self.nb)]

    # -- loose value / place construction ---------------------------------

    cdef int32_t _new_val(self, int32_t op, int32_t flags, int32_t block, int32_t aux, list args):
        cdef int32_t vid = len(self.val_op)
        self.val_op.append(op)
        self.val_flags.append(flags)
        self.val_block.append(block)
        self.val_aux.append(aux)
        self.val_args.append(args)
        self.val_dead.append(False)
        if op == OPX_PHI:
            (<list>self.block_phis[block]).append(vid)
        else:
            (<list>self.block_values[block]).append(vid)
        return vid

    cdef int32_t _get_undef(self):
        if self.undef_val < 0:
            self.undef_val = len(self.val_op)
            self.val_op.append(OPX_UNDEF)
            self.val_flags.append(0)
            self.val_block.append(self.entry)
            self.val_aux.append(-1)
            self.val_args.append([])
            self.val_dead.append(False)
        return self.undef_val

    cdef int32_t _translate_place(self, int32_t pid, int32_t block):
        cdef PlaceInfo* p = &self.src.places[pid]
        cdef int32_t kind = p.kind
        cdef int32_t flags = p.flags
        cdef int32_t br = p.block_ref
        cdef int32_t iv = p.index_val
        cdef int32_t off = p.offset
        if kind == PLACE_DYNAMIC_BLOCK:
            br = self._translate(br, block)
        if iv >= 0:
            iv = self._translate(iv, block)
        key = (kind, flags, br, iv, off)
        cached = self.sp_intern.get(key)
        if cached is not None:
            return <int32_t>cached
        cdef int32_t spid = len(self.sp_kind)
        self.sp_kind.append(kind)
        self.sp_flags.append(flags)
        self.sp_block_ref.append(br)
        self.sp_index_val.append(iv)
        self.sp_offset.append(off)
        self.sp_intern[key] = spid
        return spid

    cdef int32_t _translate(self, int32_t src_vid, int32_t block):
        cdef Instr* ins = &self.src.instrs[src_vid]
        cdef int32_t op = ins.op
        cdef int32_t pid, kind, temp, spid, astart, nargs, k
        cdef list args
        if op == OPX_CONST:
            return self._new_val(OPX_CONST, ins.flags, block, ins.aux, [])
        if op == OPX_GET:
            pid = ins.aux
            kind = self.src.places[pid].kind
            if kind == PLACE_TEMP_SCALAR:
                temp = self.src.places[pid].block_ref
                return self._read_variable(temp, block)
            spid = self._translate_place(pid, block)
            return self._new_val(OPX_GET, ins.flags, block, spid, [])
        if op == OPX_SET:
            raise AssertionError("OPX_SET in value position")
        astart = ins.arg_start
        nargs = ins.nargs
        args = [self._translate(<int32_t>self.src.args[astart + k], block) for k in range(nargs)]
        return self._new_val(op, ins.flags, block, -1, args)

    # -- Braun value numbering --------------------------------------------

    cdef int32_t _resolve(self, int32_t v):
        while v in self.subst:
            v = <int32_t>self.subst[v]
        return v

    cdef void _write_variable(self, int32_t temp, int32_t block, int32_t v):
        self.cur_def[(temp, block)] = v

    cdef int32_t _new_phi(self, int32_t block, int32_t temp):
        return self._new_val(OPX_PHI, 0, block, temp, [])

    cdef int32_t _read_variable(self, int32_t temp, int32_t block):
        cdef list chain = []
        cdef int32_t b = block
        cdef int32_t val, phi
        cdef list preds
        cdef object cd
        while True:
            cd = self.cur_def.get((temp, b))
            if cd is not None:
                val = self._resolve(<int32_t>cd)
                break
            if not <bint>self.sealed[b]:
                phi = self._new_phi(b, temp)
                (<dict>self.incomplete[b])[temp] = phi
                self._write_variable(temp, b, phi)
                val = phi
                break
            preds = <list>self.incoming[b]
            if len(preds) == 0:
                val = self._get_undef()
                self._write_variable(temp, b, val)
                break
            if len(preds) == 1:
                chain.append(b)
                b = self.src.edges[<int32_t>preds[0]].src
                continue
            phi = self._new_phi(b, temp)
            self._write_variable(temp, b, phi)
            self._add_phi_operands(temp, phi)
            val = self._resolve(phi)
            self._write_variable(temp, b, val)
            break
        cdef int32_t cb
        for cb in chain:
            self._write_variable(temp, cb, val)
        return val

    cdef void _add_phi_operands(self, int32_t temp, int32_t phi):
        cdef int32_t block = <int32_t>self.val_block[phi]
        cdef list preds = <list>self.incoming[block]
        cdef list ops = []
        cdef int32_t e, p, o, ro
        for e in preds:
            p = self.src.edges[<int32_t>e].src
            ops.append(self._read_variable(temp, p))
        self.val_args[phi] = ops
        for o in ops:
            ro = self._resolve(o)
            s = self.phi_users.get(ro)
            if s is None:
                s = set()
                self.phi_users[ro] = s
            s.add(phi)
        self._try_remove_trivial(phi)

    cdef void _try_remove_trivial(self, int32_t phi):
        cdef list worklist = [phi]
        cdef int32_t p, o, r, same, u
        cdef bint trivial, saw_undef
        while worklist:
            p = <int32_t>worklist.pop()
            if <bint>self.val_dead[p]:
                continue
            if <int32_t>self.val_op[p] != OPX_PHI:
                continue
            same = -1
            trivial = True
            saw_undef = False
            for o in <list>self.val_args[p]:
                r = self._resolve(<int32_t>o)
                # Skip self-references AND UNDEF operands: phi(UNDEF, v) == v
                # (the undef path is genuinely undefined -- a provably-dead access
                # per OPTIMIZER_REWRITE.md 6.1 -- so picking v is valid). All-undef
                # collapses to UNDEF below.
                if r == p or r == same:
                    continue
                if self.undef_val >= 0 and r == self.undef_val:
                    saw_undef = True
                    continue
                if same != -1:
                    trivial = False
                    break
                same = r
            if not trivial:
                continue
            if same == -1:
                same = self._get_undef()
            elif saw_undef:
                # phi(UNDEF, v) -> v: v is now used at the phi's merge point where
                # it is only available on the non-undef (live) path, so its def no
                # longer strictly dominates every use. Record it so verify() can
                # tolerate the widened (dead-path-relaxed) availability.
                self.undef_widened.add(same)
            self.subst[p] = same
            self.val_dead[p] = True
            users = self.phi_users.get(p)
            if users:
                su = self.phi_users.get(same)
                if su is None:
                    su = set()
                    self.phi_users[same] = su
                for u in users:
                    if u == p:
                        continue
                    su.add(u)
                    if (not <bint>self.val_dead[u]) and <int32_t>self.val_op[u] == OPX_PHI:
                        worklist.append(u)

    cdef void _seal(self, int32_t block):
        self.sealed[block] = True
        cdef dict inc = <dict>self.incomplete[block]
        cdef list items = list(inc.items())
        inc.clear()
        cdef int32_t temp, phi
        for temp, phi in items:
            self._add_phi_operands(temp, phi)

    # -- fill a block: process its source statement roots + test ----------

    cdef void _fill(self, int32_t block):
        cdef int32_t istart = self.src.blocks[block].instr_start
        cdef int32_t icount = self.src.blocks[block].instr_count
        cdef int32_t i, op, pid, kind, rhs, v, temp, spid, tv
        for i in range(istart, istart + icount):
            if not (self.src.instrs[i].flags & FLAG_STMT_ROOT):
                continue
            op = self.src.instrs[i].op
            if op == OPX_SET:
                pid = self.src.instrs[i].aux
                kind = self.src.places[pid].kind
                rhs = <int32_t>self.src.args[self.src.instrs[i].arg_start]
                v = self._translate(rhs, block)
                if kind == PLACE_TEMP_SCALAR:
                    temp = self.src.places[pid].block_ref
                    self._write_variable(temp, block, v)
                else:
                    spid = self._translate_place(pid, block)
                    self._new_val(OPX_SET, FLAG_SIDE_EFFECT | FLAG_PINNED | FLAG_STMT_ROOT, block, spid, [v])
            else:
                v = self._translate(i, block)
                self.val_flags[v] = <int32_t>self.val_flags[v] | FLAG_STMT_ROOT
        tv = self.src.blocks[block].test_val
        if tv >= 0:
            self.block_test[block] = self._translate(tv, block)
        else:
            self.block_test[block] = -1

    cdef void _run(self):
        cdef int32_t b, s
        old_limit = _sys.getrecursionlimit()
        _sys.setrecursionlimit(max(old_limit, 100000 + self.nb * 8))
        try:
            for b in range(self.nb):
                if <int32_t>self.unfilled_preds[b] == 0:
                    self._seal(b)
            for b in range(self.nb):
                self._fill(b)
                self.filled[b] = True
                for s in <list>self.succs_distinct[b]:
                    self.unfilled_preds[s] = <int32_t>self.unfilled_preds[s] - 1
                    if <int32_t>self.unfilled_preds[s] == 0 and not <bint>self.sealed[s]:
                        self._seal(<int32_t>s)
        finally:
            _sys.setrecursionlimit(old_limit)

    # -- compact loose SSA into a fresh tight arena Func -------------------

    cdef Func _compact(self):
        cdef Func src = self.src
        cdef Func dst = Func()
        cdef int32_t nb = self.nb
        cdef int32_t b, v, ni, k, o

        # Per-block emission order: [undef in entry] + live phis + live values.
        cdef list order = [[] for _ in range(nb)]
        cdef list ob
        for b in range(nb):
            ob = order[b]
            if b == self.entry and self.undef_val >= 0:
                ob.append(self.undef_val)
            for v in <list>self.block_phis[b]:
                if not <bint>self.val_dead[v]:
                    ob.append(v)
            for v in <list>self.block_values[b]:
                if not <bint>self.val_dead[v]:
                    ob.append(v)

        # Assign new instr indices + count args; record each block's start index.
        cdef dict newidx = {}
        cdef list block_start = [0] * nb
        cdef int32_t next_idx = 0
        cdef int32_t total_args = 0
        for b in range(nb):
            block_start[b] = next_idx
            for v in <list>order[b]:
                newidx[v] = next_idx
                next_idx += 1
                total_args += len(<list>self.val_args[v])
        cdef int32_t total_instrs = next_idx

        # Places (remap dynamic block_ref / index_val loose vids to new indices).
        cdef int32_t npl = len(self.sp_kind)
        cdef int32_t spid, kind
        cdef int32_t br, iv
        for spid in range(npl):
            kind = <int32_t>self.sp_kind[spid]
            br = <int32_t>self.sp_block_ref[spid]
            iv = <int32_t>self.sp_index_val[spid]
            if kind == PLACE_DYNAMIC_BLOCK:
                br = <int32_t>newidx[self._resolve(br)]
            if iv >= 0:
                iv = <int32_t>newidx[self._resolve(iv)]
            _add_place(dst, <uint8_t>kind, <uint8_t>self.sp_flags[spid], br, iv, <int32_t>self.sp_offset[spid])

        # consts + temps carry over 1:1 (const/temp ids stay valid).
        if src.n_consts > 0:
            dst.consts = <double*>malloc(<size_t>src.n_consts * sizeof(double))
            if dst.consts == NULL:
                raise MemoryError()
            memcpy(dst.consts, src.consts, <size_t>src.n_consts * sizeof(double))
        dst.n_consts = src.n_consts
        dst.cap_consts = src.n_consts
        dst._const_intern = dict(src._const_intern)
        if src.n_temps > 0:
            dst.temps = <TempInfo*>malloc(<size_t>src.n_temps * sizeof(TempInfo))
            if dst.temps == NULL:
                raise MemoryError()
            memcpy(dst.temps, src.temps, <size_t>src.n_temps * sizeof(TempInfo))
        dst.n_temps = src.n_temps
        dst.cap_temps = src.n_temps
        dst.names = list(src.names)
        dst._temp_intern = dict(src._temp_intern)
        dst._block_enum_by_id = dict(src._block_enum_by_id)
        dst.blocks_type = src.blocks_type
        dst.callback = src.callback
        dst._block_map = dict(src._block_map)

        # blocks + instrs + args.
        dst.blocks = <BlockInfo*>malloc(<size_t>(nb if nb > 0 else 1) * sizeof(BlockInfo))
        if dst.blocks == NULL:
            raise MemoryError()
        dst.n_blocks = nb
        dst.cap_blocks = nb
        dst.entry_block = self.entry
        dst.is_ssa = True

        dst.instrs = <Instr*>malloc(<size_t>(total_instrs if total_instrs > 0 else 1) * sizeof(Instr))
        dst.args = <uint32_t*>malloc(<size_t>(total_args if total_args > 0 else 1) * sizeof(uint32_t))
        if dst.instrs == NULL or dst.args == NULL:
            raise MemoryError()
        dst.n_instrs = total_instrs
        dst.cap_instrs = total_instrs
        dst.n_args = total_args
        dst.cap_args = total_args

        cdef int32_t arg_cursor = 0
        cdef int32_t op, flags, aux, nargs, nphi, tv, phi_first
        cdef list raw_args
        for b in range(nb):
            ob = order[b]
            dst.blocks[b].instr_start = <int32_t>block_start[b]
            dst.blocks[b].instr_count = len(ob)
            dst.blocks[b].rpo = b
            dst.blocks[b].idom = -1
            # phis are the contiguous OPX_PHI run (after any leading OPX_UNDEF).
            nphi = 0
            phi_first = -1
            for v in ob:
                if <int32_t>self.val_op[v] == OPX_PHI:
                    if phi_first == -1:
                        phi_first = <int32_t>newidx[v]
                    nphi += 1
            dst.blocks[b].phi_count = nphi
            dst.blocks[b].phi_start = phi_first if phi_first != -1 else dst.blocks[b].instr_start
            for v in ob:
                ni = <int32_t>newidx[v]
                op = <int32_t>self.val_op[v]
                flags = <int32_t>self.val_flags[v]
                aux = <int32_t>self.val_aux[v]
                raw_args = <list>self.val_args[v]
                nargs = len(raw_args)
                dst.instrs[ni].op = <uint16_t>op
                dst.instrs[ni].flags = <uint8_t>flags
                dst.instrs[ni].block = b
                dst.instrs[ni].arg_start = arg_cursor
                dst.instrs[ni].nargs = <int16_t>nargs
                dst.instrs[ni].aux = aux
                for o in raw_args:
                    dst.args[arg_cursor] = <uint32_t>(<int32_t>newidx[self._resolve(<int32_t>o)])
                    arg_cursor += 1
            tv = <int32_t>self.block_test[b]
            if tv >= 0:
                dst.blocks[b].test_val = <int32_t>newidx[self._resolve(tv)]
            else:
                dst.blocks[b].test_val = -1

        # edges: identical CFG topology, copied verbatim.
        dst.edges = <Edge*>malloc(<size_t>(src.n_edges if src.n_edges > 0 else 1) * sizeof(Edge))
        if dst.edges == NULL:
            raise MemoryError()
        memcpy(dst.edges, src.edges, <size_t>src.n_edges * sizeof(Edge))
        dst.n_edges = src.n_edges
        dst.cap_edges = src.n_edges
        for b in range(nb):
            dst.blocks[b].edge_start = src.blocks[b].edge_start
            dst.blocks[b].edge_count = src.blocks[b].edge_count

        dst.undef_val = (<int32_t>newidx[self.undef_val]) if self.undef_val >= 0 else -1

        # UNDEF-widened values (mapped to final indices) so verify() tolerates
        # their dead-path-relaxed uses; carried for downstream passes too.
        cdef set widened = set()
        cdef int32_t wv
        for wv in self.undef_widened:
            widened.add(<int32_t>newidx[self._resolve(wv)])
        dst._ssa_undef = widened

        # Dominators (fills BlockInfo.idom) -- needed by verify() in SSA form.
        compute_dominators(dst)
        return dst


cdef PlaceInfo* _grow_places(PlaceInfo* buf, int32_t* cap, int32_t need) except NULL:
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


cdef int32_t _add_place(Func dst, uint8_t kind, uint8_t flags, int32_t block_ref,
                        int32_t index_val, int32_t offset) except -1:
    cdef int32_t pid = dst.n_places
    dst.places = _grow_places(dst.places, &dst.cap_places, pid + 1)
    dst.places[pid].kind = kind
    dst.places[pid].flags = flags
    dst.places[pid].block_ref = block_ref
    dst.places[pid].index_val = index_val
    dst.places[pid].offset = offset
    dst.n_places = pid + 1
    return pid


cdef Func build_ssa(Func func):
    """Construct value-based SSA over ``func`` (Braun); returns a fresh SSA Func."""
    cdef _SSABuilder builder = _SSABuilder(func)
    builder._run()
    return builder._compact()


def run_ssa(entry, mode=None, callback=None):
    """marshal_in -> cfg_cleanup -> build_ssa -> to_basic_blocks (SSA inspection)."""
    cdef Func func = cfg_cleanup(<Func>marshal_in(entry, mode, callback), False)
    cdef Func ssa = build_ssa(func)
    ssa.verify()
    return to_basic_blocks(ssa)


# ==========================================================================
# Out-of-SSA -- naive, correct-first (OPTIMIZER_REWRITE.md 7.4.2 simple version).
#
# Every SSA value is materialized to a fresh size-1 temp (except constants, which
# inline, single-use same-block pure values, which fold into their consumer, and
# the shared UNDEF value, which lowers to ONE shared never-written temp). Phis
# become parallel copies at the predecessor's exit -- placed at the end of the
# predecessor when it has a single successor, else on a freshly split critical
# edge -- and the parallel copies are sequentialized (fresh temps break cycles,
# e.g. a swap phi(a<-b, b<-a)). No coalescing / treeify: correctness only (a later
# agent adds those on this SSA form). The output is a normal non-SSA arena Func
# (self-contained blocks, scalar temps as OPX_GET/OPX_SET, no phis) that
# lower.pyx / emit.pyx consume unchanged.
# ==========================================================================


def _seq_parallel_copies(list copies, make_fresh):
    """Sequentialize a parallel copy set (Boissinot et al.) with fresh cycle temps.

    ``copies`` is a list of ``(dst, src)`` with distinct ``dst`` keys (parallel
    semantics: all reads happen before all writes). ``make_fresh()`` yields a
    fresh temp key. Returns an ordered ``(dst, src)`` list for sequential
    execution. dst keys are temp ids (ints); src keys may be ints (temps) or
    tuples (const/undef leaves that are never dsts).
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


cdef TempInfo* _grow_temps(TempInfo* buf, int32_t* cap, int32_t need) except NULL:
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


cdef class _UnSSA:
    cdef Func src
    cdef Func dst
    cdef int32_t nb
    cdef int32_t n_out
    # use analysis (per SSA value id)
    cdef list use_count
    cdef list inline_ok
    cdef list materialize
    cdef list drop
    cdef list value_temp        # per value -> dst temp id (-1 if none)
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
        self.inline_ok = [True] * ni
        self.materialize = [False] * ni
        self.drop = [False] * ni
        self.value_temp = [-1] * ni
        cdef int32_t e, b
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

    # -- use analysis ------------------------------------------------------

    def _record_use(self, int32_t v, int32_t ublock, bint is_phi):
        self.use_count[v] = <int32_t>self.use_count[v] + 1
        if is_phi or <int32_t>self.use_count[v] > 1 or ublock != <int32_t>self.src.instrs[v].block:
            self.inline_ok[v] = False

    def _gen_place_uses(self, int32_t pid, int32_t ublock):
        cdef int32_t kind = self.src.places[pid].kind
        if kind == PLACE_DYNAMIC_BLOCK:
            self._record_use(self.src.places[pid].block_ref, ublock, False)
        if self.src.places[pid].index_val >= 0:
            self._record_use(self.src.places[pid].index_val, ublock, False)

    def _analyze(self):
        cdef Func src = self.src
        cdef int32_t i, b, op, k, astart, nargs, pi, pstart, pcount, e, ksrc
        for i in range(src.n_instrs):
            op = src.instrs[i].op
            b = src.instrs[i].block
            if op == OPX_PHI:
                # operand uses live at the predecessor exit (per incoming edge).
                astart = src.instrs[i].arg_start
                pstart = src.blocks[b].phi_start
                for k in range(src.instrs[i].nargs):
                    e = <int32_t>(<list>self.incoming[b])[k]
                    self._record_use(<int32_t>src.args[astart + k], src.edges[e].src, True)
                continue
            if op == OPX_UNDEF or op == OPX_CONST:
                continue
            astart = src.instrs[i].arg_start
            nargs = src.instrs[i].nargs
            for k in range(nargs):
                self._record_use(<int32_t>src.args[astart + k], b, False)
            if op == OPX_GET or op == OPX_SET:
                self._gen_place_uses(src.instrs[i].aux, b)
        for b in range(src.n_blocks):
            if src.blocks[b].test_val >= 0:
                self._record_use(src.blocks[b].test_val, b, False)

        # Materialization decision.
        cdef int32_t flags
        cdef bint pure, side, pinned, inlinable
        for i in range(src.n_instrs):
            op = src.instrs[i].op
            if op == OPX_PHI or op == OPX_UNDEF or op == OPX_CONST:
                continue
            if src.instrs[i].flags & FLAG_STMT_ROOT:
                continue
            flags = src.instrs[i].flags
            side = (flags & FLAG_SIDE_EFFECT) != 0
            pinned = (flags & FLAG_PINNED) != 0
            pure = (flags & FLAG_PURE) != 0
            if <int32_t>self.use_count[i] == 0 and not side:
                self.drop[i] = True
                continue
            inlinable = pure and (not pinned) and (not side) and <int32_t>self.use_count[i] == 1 and <bint>self.inline_ok[i]
            self.materialize[i] = not inlinable

    # -- temps / places ----------------------------------------------------

    def _new_temp(self, int32_t size):
        cdef Func dst = self.dst
        cdef int32_t tid = dst.n_temps
        dst.temps = _grow_temps(dst.temps, &dst.cap_temps, tid + 1)
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
        cdef int32_t pid = _add_place(self.dst, PLACE_TEMP_SCALAR, PLACE_WRITABLE, temp, -1, 0)
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
        dst.temps = _grow_temps(dst.temps, &dst.cap_temps, tid + 1)
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
            # If SSA collapsed the pointer to a compile-time constant block id
            # (a scalar temp holding a block id dissolved), lower it to a plain
            # real-block place -- a non-SSA BasicBlock cannot carry an IRConst as
            # its block, and this is exactly what finalize would fold it to.
            if src.instrs[br].op == OPX_CONST:
                kind = PLACE_REAL_BLOCK
                flags = 0
                br = <int32_t>int(src.consts[src.instrs[br].aux])
            else:
                br = self._emit_ref(br, block)
        if iv >= 0:
            iv = self._emit_ref(iv, block)
        return _add_place(self.dst, <uint8_t>kind, <uint8_t>flags, br, iv, off)

    # -- tree emission -----------------------------------------------------

    def _emit_ref(self, int32_t v, int32_t block):
        # A value in operand position -> a dst instruction id.
        cdef Func src = self.src
        cdef int32_t op = src.instrs[v].op
        if op == OPX_CONST:
            return self.dst._emit(OPX_CONST, src.instrs[v].flags, block, src.instrs[v].aux, [])
        if op == OPX_UNDEF:
            return self.dst._emit(OPX_GET, FLAG_PINNED, block, self._get_undef_place(), [])
        if op == OPX_PHI or <bint>self.materialize[v]:
            return self.dst._emit(OPX_GET, FLAG_PINNED, block, self._scalar_place_of(<int32_t>self.value_temp[v]), [])
        return self._emit_tree(v, block)

    def _emit_tree(self, int32_t v, int32_t block):
        # Build v's expression fresh (for a materialized def RHS or an inlined use).
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
        return self.dst._emit(op, src.instrs[v].flags, block, -1, args)

    # -- phi copies --------------------------------------------------------

    def _make_cycle_temp(self):
        return self._new_temp(1)

    def _emit_copies(self, int32_t edge, int32_t block):
        cdef Func src = self.src
        cdef int32_t d = src.edges[edge].dst
        cdef int32_t k = <int32_t>self.edge_pos[edge]
        cdef int32_t pstart = src.blocks[d].phi_start
        cdef int32_t pcount = src.blocks[d].phi_count
        cdef int32_t pi, operand, opnd_op, dtemp
        cdef list copies = []
        for pi in range(pstart, pstart + pcount):
            operand = <int32_t>src.args[src.instrs[pi].arg_start + k]
            opnd_op = src.instrs[operand].op
            if opnd_op == OPX_CONST:
                skey = ("c", src.instrs[operand].aux, <int32_t>src.instrs[operand].flags)
            elif opnd_op == OPX_UNDEF:
                skey = ("u",)
            else:
                skey = <int32_t>self.value_temp[operand]
            dtemp = <int32_t>self.value_temp[pi]
            copies.append((dtemp, skey))
        cdef list seq = _seq_parallel_copies(copies, self._make_cycle_temp)
        cdef int32_t ref, dplace
        for dtemp, skey in seq:
            dplace = self._scalar_place_of(<int32_t>dtemp)
            if isinstance(skey, tuple):
                if skey[0] == "c":
                    ref = self.dst._emit(OPX_CONST, <int32_t>skey[2], block, <int32_t>skey[1], [])
                else:
                    ref = self.dst._emit(OPX_GET, FLAG_PINNED, block, self._get_undef_place(), [])
            else:
                ref = self.dst._emit(OPX_GET, FLAG_PINNED, block, self._scalar_place_of(<int32_t>skey), [])
            self.dst._emit(OPX_SET, FLAG_SIDE_EFFECT | FLAG_PINNED | FLAG_STMT_ROOT, block, dplace, [ref])

    # -- driver ------------------------------------------------------------

    def _plan(self):
        # Assign temps to phis + materialized values; plan critical-edge splits.
        cdef Func src = self.src
        cdef int32_t i, op, b, e, d
        for i in range(src.n_instrs):
            op = src.instrs[i].op
            if op == OPX_PHI:
                self.value_temp[i] = self._new_temp(1)
            elif op == OPX_CONST or op == OPX_UNDEF:
                continue
            elif src.instrs[i].flags & FLAG_STMT_ROOT:
                continue
            elif <bint>self.materialize[i]:
                self.value_temp[i] = self._new_temp(1)
        # Splits: an edge into a phi-block whose source has >1 distinct successor.
        self.n_out = self.nb
        for b in range(src.n_blocks):
            for e in range(src.blocks[b].edge_start, src.blocks[b].edge_start + src.blocks[b].edge_count):
                d = src.edges[e].dst
                if src.blocks[d].phi_count > 0 and len(<list>self.distinct_succ[b]) > 1:
                    self.edge_split[e] = self.n_out
                    self.split_edge.append(e)
                    self.n_out += 1

    def build(self):
        cdef Func src = self.src
        cdef Func dst = self.dst
        self._analyze()
        self._plan()

        # consts carry over 1:1 (const ids stay valid for OPX_CONST aux).
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

        cdef int32_t b, i, op, istart, tv, di, e
        # Original blocks: value materializations / roots, then phi copies, then test.
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
                        # bare side-effecting root (operands emitted then the op).
                        self._emit_tree_root(i, b)
                elif <bint>self.drop[i]:
                    continue
                elif <bint>self.materialize[i]:
                    val = self._emit_tree(i, b)
                    dst._emit(OPX_SET, FLAG_SIDE_EFFECT | FLAG_PINNED | FLAG_STMT_ROOT, b,
                              self._scalar_place_of(<int32_t>self.value_temp[i]), [val])
            # phi copies at the end of b for single-successor phi targets.
            if len(<list>self.distinct_succ[b]) == 1:
                di = <int32_t>(<list>self.distinct_succ[b])[0]
                if src.blocks[di].phi_count > 0:
                    # representative edge b -> di (parallel edges carry equal operands).
                    e = self._first_edge(b, di)
                    self._emit_copies(e, b)
            tv = src.blocks[b].test_val
            if tv >= 0:
                dst.blocks[b].test_val = self._emit_ref(tv, b)
            else:
                dst.blocks[b].test_val = -1
            dst.blocks[b].instr_count = dst.n_instrs - istart

        # Split blocks: just the phi copies for their edge, then jump to dst.
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

        # Edges (contiguous per output block).
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

    def _emit_tree_root(self, int32_t v, int32_t block):
        cdef Func src = self.src
        cdef int32_t astart = src.instrs[v].arg_start
        cdef int32_t nargs = src.instrs[v].nargs
        cdef int32_t k
        cdef list args = [self._emit_ref(<int32_t>src.args[astart + k], block) for k in range(nargs)]
        cdef int32_t r = self.dst._emit(src.instrs[v].op, src.instrs[v].flags, block, -1, args)
        self.dst.instrs[r].flags = <uint8_t>(self.dst.instrs[r].flags | FLAG_STMT_ROOT)

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


cdef Func out_of_ssa(Func func):
    """Lower an SSA Func back to non-SSA form (naive split-edges + parallel copies)."""
    cdef _UnSSA u = _UnSSA(func)
    return <Func>u.build()


def run_unssa(entry, mode=None, callback=None):
    """marshal_in -> cfg_cleanup -> build_ssa -> out_of_ssa -> to_basic_blocks."""
    cdef Func func = cfg_cleanup(<Func>marshal_in(entry, mode, callback), False)
    cdef Func ssa = build_ssa(func)
    ssa.verify()
    cdef Func lowered = out_of_ssa(ssa)
    lowered.verify()
    return to_basic_blocks(lowered)
