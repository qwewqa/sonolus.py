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

from libc.stdint cimport int32_t, uint8_t, uint16_t
from libc.stdlib cimport calloc, free, malloc, realloc
from libc.string cimport memcpy

from sonolus.backend._opt.ir cimport (
    BlockInfo,
    Edge,
    EDGE_COND_NONE,
    EDGE_COND_VALUE,
    FLAG_STMT_ROOT,
    Func,
    Instr,
    PlaceInfo,
    PLACE_DYNAMIC_BLOCK,
    TempInfo,
)
from sonolus.backend._opt._ops_gen cimport OPX_CONST, OPX_GET, OPX_SET

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
