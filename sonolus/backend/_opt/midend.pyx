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

from libc.stdint cimport int16_t, int32_t, uint8_t, uint16_t, uint32_t, uint64_t
from libc.stdlib cimport calloc, free, malloc, realloc
from libc.string cimport memcpy
from libc.math cimport floor, isinf, isnan, signbit

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
    PLACE_RUNTIME_CONST,
    PLACE_WRITABLE,
    TempInfo,
)
from sonolus.backend._opt._ops_gen cimport (
    OPX_CONST,
    OPX_GET,
    OPX_PHI,
    OPX_SET,
    OPX_UNDEF,
    OP_Add,
    OP_And,
    OP_DecrementPostPointed,
    OP_DecrementPostShifted,
    OP_Divide,
    OP_Equal,
    OP_GetPointed,
    OP_GetShifted,
    OP_Greater,
    OP_GreaterOr,
    OP_IncrementPostPointed,
    OP_IncrementPostShifted,
    OP_Less,
    OP_LessOr,
    OP_Max,
    OP_Min,
    OP_Mod,
    OP_Multiply,
    OP_Negate,
    OP_Not,
    OP_NotEqual,
    OP_Or,
    OP_Power,
    OP_Rem,
    OP_RUNTIME_COUNT,
    OP_SetAddPointed,
    OP_SetAddShifted,
    OP_SetDividePointed,
    OP_SetDivideShifted,
    OP_SetModPointed,
    OP_SetModShifted,
    OP_SetMultiplyPointed,
    OP_SetMultiplyShifted,
    OP_SetPointed,
    OP_SetPowerPointed,
    OP_SetPowerShifted,
    OP_SetRemPointed,
    OP_SetRemShifted,
    OP_SetShifted,
    OP_SetSubtractPointed,
    OP_SetSubtractShifted,
    OP_Subtract,
    SONOLUS_OP_FOLDABLE,
)
from sonolus.backend._opt.analysis cimport Dominators, LoopForest, compute_dominators, compute_loops
from sonolus.backend._opt.kernels cimport FOLD_OK, fold_op

from sonolus.backend._opt.ir import marshal_in, register_phase, to_basic_blocks


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

    # Cached raw C pointers into the (read-only) source arena, so the nogil
    # transform passes never touch the ``self.src`` Python object attribute.
    cdef Instr* src_instrs
    cdef BlockInfo* src_blocks
    cdef double* src_consts

    # ---- construction ------------------------------------------------------

    def __cinit__(self, Func src, bint phi_safe):
        self.src = src
        self.src_instrs = src.instrs
        self.src_blocks = src.blocks
        self.src_consts = src.consts
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

    cdef void _ensure_edge_cap(self, int32_t need) except * nogil:
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
            with gil:
                raise MemoryError()
        self.cap_e = nc

    cdef void _add_edge(self, int32_t s, int32_t d, uint8_t ck, uint8_t ci, double cond) except * nogil:
        cdef int32_t k = self.n_e
        self._ensure_edge_cap(k + 1)
        self.e_src[k] = s
        self.e_dst[k] = d
        self.e_ck[k] = ck
        self.e_ci[k] = ci
        self.e_cond[k] = cond
        self.e_alive[k] = 1
        self.n_e = k + 1

    cdef int32_t _new_cn(self, int32_t src_block) except -1 nogil:
        cdef int32_t k = self.n_cn
        cdef int32_t nc
        if k + 1 > self.cap_cn:
            nc = self.cap_cn if self.cap_cn > 0 else 8
            while nc < k + 1:
                nc *= 2
            self.cn_src = <int32_t*>realloc(self.cn_src, <size_t>nc * sizeof(int32_t))
            self.cn_next = <int32_t*>realloc(self.cn_next, <size_t>nc * sizeof(int32_t))
            if self.cn_src == NULL or self.cn_next == NULL:
                with gil:
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

    cdef int32_t _chain_nstmt(self, int32_t head) noexcept nogil:
        cdef int32_t total = 0
        cdef int32_t node = self.chain_head[head]
        while node != -1:
            total += self.nstmt[self.cn_src[node]]
            node = self.cn_next[node]
        return total

    cdef void _append_chain_transfer(self, int32_t a, int32_t b) noexcept nogil:
        # Splice b's chain onto a (b is about to be killed; reuse its nodes).
        self.cn_next[self.chain_tail[a]] = self.chain_head[b]
        self.chain_tail[a] = self.chain_tail[b]

    cdef void _append_chain_copy(self, int32_t a, int32_t b) except * nogil:
        # Append a fresh copy of b's chain onto a (b stays live -- tail-dup).
        cdef int32_t node = self.chain_head[b]
        cdef int32_t nn
        while node != -1:
            nn = self._new_cn(self.cn_src[node])
            self.cn_next[self.chain_tail[a]] = nn
            self.chain_tail[a] = nn
            node = self.cn_next[node]

    # ---- edge helpers ------------------------------------------------------

    cdef bint _has_value_edge(self, int32_t h) noexcept nogil:
        cdef int32_t i
        for i in range(self.n_e):
            if self.e_alive[i] and self.e_src[i] == h and self.e_ck[i] == EDGE_COND_VALUE:
                return True
        return False

    cdef int32_t _single_none_succ(self, int32_t h, int32_t* edge_out) noexcept nogil:
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

    cdef bint _const_fold(self) except -1 nogil:
        cdef bint changed = False
        cdef int32_t h, i, tv, tail_block, live_edge
        cdef double tval
        for h in range(self.nb):
            if not self.alive[h] or not self.is_head[h]:
                continue
            if not self._has_value_edge(h):
                continue
            tail_block = self.cn_src[self.chain_tail[h]]
            tv = self.src_blocks[tail_block].test_val
            if tv < 0 or self.src_instrs[tv].op != OPX_CONST:
                continue
            tval = self.src_consts[self.src_instrs[tv].aux]
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

    cdef bint _dedup(self) except -1 nogil:
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

    cdef bint _thread_forwarders(self) except -1 nogil:
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

    cdef bint _merge_single(self) except -1 nogil:
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

    cdef bint _taildup(self) except -1 nogil:
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

    cdef bint _prune_unreachable(self) except -1 nogil:
        cdef bint changed = False
        cdef int32_t i, cur, d, sp
        cdef uint8_t* visited = <uint8_t*>calloc(self.nb, sizeof(uint8_t))
        cdef int32_t* stack = <int32_t*>malloc(<size_t>self.nb * sizeof(int32_t))
        if visited == NULL or stack == NULL:
            free(visited)
            free(stack)
            with gil:
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
        # The six pure-C transforms run in a ``nogil`` region (they operate only on
        # the flat logical-edge / chain C arrays and the cached read-only source
        # pointers), which is what lets the per-callback build pool parallelise this
        # pass -- cfg_cleanup's ``run()`` is ~94% of the pass and >50% of the whole
        # optimize phase. ``_canonicalize_exits`` (RPO over Python lists) stays under
        # the GIL; it is ~1/7 of the loop and left untouched to keep the byte-exact
        # exit-block ordering. The GIL is toggled once per fixpoint iteration only.
        cdef bint changed = True
        cdef long iters = 0
        cdef long cap = <long>self.nb * self.nb + self.n_e + 100000
        while changed:
            changed = False
            with nogil:
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
        # Braun trivial-phi removal: a phi collapses only when it has at most one
        # distinct operand besides self-references. UNDEF is treated as a NORMAL
        # distinct operand here -- deliberately NOT skipped. Collapsing
        # phi(UNDEF, v) -> v is only sound when the UNDEF path is provably dead,
        # which construction cannot prove; skipping UNDEF (the old behaviour)
        # mis-folds *live* uninitialized merges (a loop preheader, or an untaken
        # switch arm) to v, diverging from the oracle, and -- for a loop back-edge
        # v -- produces a value-graph cycle / def-before-use arena. Keeping the phi
        # is always sound; the provably-dead collapse now lives in SCCP, which
        # drops the UNDEF operand's incoming edge via edge executability (7.2.2).
        # Consequently ``undef_widened`` / ``_ssa_undef`` stay empty.
        cdef list worklist = [phi]
        cdef int32_t p, o, r, same, u
        cdef bint trivial
        while worklist:
            p = <int32_t>worklist.pop()
            if <bint>self.val_dead[p]:
                continue
            if <int32_t>self.val_op[p] != OPX_PHI:
                continue
            same = -1
            trivial = True
            for o in <list>self.val_args[p]:
                r = self._resolve(<int32_t>o)
                if r == p or r == same:
                    continue
                if same != -1:
                    trivial = False
                    break
                same = r
            if not trivial:
                continue
            if same == -1:
                # phi refers only to itself (unreachable) -> UNDEF.
                same = self._get_undef()
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


# ==========================================================================
# M2 mid-end: SCCP -> simplify/GVN -> DCE, driven once (or twice) per round.
#
# All three passes operate on the value-based SSA arena produced by ``build_ssa``
# and leave it in valid SSA form (verify()-green), so ``out_of_ssa`` consumes the
# optimized arena unchanged. SCCP and DCE produce fresh compacted arenas (via the
# shared ``_build_compacted`` rebuilder); GVN rewrites operands in place.
#
# The passes run correctness-first under the GIL (like ``build_ssa`` /
# ``out_of_ssa``), using Python working state; the orchestrator wires them into
# nogil-friendly driver stages later. See OPTIMIZER_REWRITE.md 7.2.2-7.2.7.
# ==========================================================================

cdef object _MISSING = object()


cdef inline uint64_t _dbits(double d) noexcept nogil:
    # Bit pattern with a single canonical quiet-NaN, so lattice equality is
    # bitwise (the fix for NaN-through-a-loop-phi nontermination) and -0.0 stays
    # distinct from +0.0.
    if d != d:
        return <uint64_t>0x7FF8000000000000
    return (<uint64_t*>&d)[0]


cdef inline double _bits_to_d(uint64_t b) noexcept nogil:
    return (<double*>&b)[0]


cdef enum:
    _LAT_TOP = 0      # nothing known yet (old UNDEF)
    _LAT_CONST = 1    # a single f64
    _LAT_SET = 2      # a small (<=100) set of f64 (branch-correlated)
    _LAT_BOTTOM = 3   # not-a-constant (old NAC)


# --------------------------------------------------------------------------
# Structural boolean-ness (value is provably 0/1) -- shared by the SCCP And/Or
# short-circuit exception and the GVN Not(Not(b)) identity. Fixpoint over:
# const 0/1, comparisons, Not, And/Or/phi of boolean values (7.2.2).
# --------------------------------------------------------------------------

cdef list _compute_bool(Func f):
    cdef int32_t n = f.n_instrs
    cdef list isb = [False] * n
    cdef int32_t i, op, a, k, astart, nargs
    cdef bint changed = True
    cdef bint bval
    cdef double cv
    while changed:
        changed = False
        for i in range(n):
            if <bint>isb[i]:
                continue
            op = f.instrs[i].op
            bval = False
            if op == OPX_CONST:
                cv = f.consts[f.instrs[i].aux]
                if cv == 0.0 or cv == 1.0:
                    bval = True
            elif (op == OP_Equal or op == OP_NotEqual or op == OP_Greater
                  or op == OP_GreaterOr or op == OP_Less or op == OP_LessOr or op == OP_Not):
                bval = True
            elif op == OP_And or op == OP_Or or op == OPX_PHI:
                bval = True
                astart = f.instrs[i].arg_start
                nargs = f.instrs[i].nargs
                for k in range(nargs):
                    a = <int32_t>f.args[astart + k]
                    if not <bint>isb[a]:
                        bval = False
                        break
            if bval:
                isb[i] = True
                changed = True
    return isb


# --------------------------------------------------------------------------
# SCCP -- Wegman-Zadeck sparse conditional constant propagation (7.2.2).
# --------------------------------------------------------------------------

cdef class _SCCP:
    cdef Func f
    cdef list lk            # per vid: lattice kind
    cdef list lc            # per vid: const double (kind==CONST)
    cdef list ls            # per vid: frozenset[int] of canon-bits (kind==SET)
    cdef list is_bool
    cdef uint8_t* executable    # [n_edges]
    cdef uint8_t* reached       # [n_blocks]
    cdef list use_of            # per vid: list[int]; >=0 value user, <0 encodes -(block+1) test user
    cdef list incoming          # per block: list of edge ids (ascending == contract)
    cdef list flow_wl
    cdef list val_wl
    cdef list test_wl

    def __cinit__(self, Func f):
        self.f = f
        cdef int32_t n = f.n_instrs
        cdef int32_t nb = f.n_blocks
        cdef int32_t ne = f.n_edges
        self.lk = [_LAT_TOP] * n
        self.lc = [0.0] * n
        self.ls = [None] * n
        self.is_bool = _compute_bool(f)
        self.executable = <uint8_t*>calloc(ne if ne > 0 else 1, sizeof(uint8_t))
        self.reached = <uint8_t*>calloc(nb if nb > 0 else 1, sizeof(uint8_t))
        if self.executable == NULL or self.reached == NULL:
            raise MemoryError()
        self.flow_wl = []
        self.val_wl = []
        self.test_wl = []
        self.incoming = [[] for _ in range(nb)]
        cdef int32_t e, b, i, k, op, astart, nargs, a, pid, tv
        for e in range(ne):
            (<list>self.incoming[f.edges[e].dst]).append(e)
        # SSA def-use edges (value users + block-test users).
        self.use_of = [[] for _ in range(n)]
        for i in range(n):
            op = f.instrs[i].op
            if op == OPX_CONST or op == OPX_UNDEF:
                continue
            astart = f.instrs[i].arg_start
            nargs = f.instrs[i].nargs
            for k in range(nargs):
                a = <int32_t>f.args[astart + k]
                (<list>self.use_of[a]).append(i)
            if op == OPX_GET or op == OPX_SET:
                pid = f.instrs[i].aux
                if f.places[pid].kind == PLACE_DYNAMIC_BLOCK:
                    (<list>self.use_of[f.places[pid].block_ref]).append(i)
                if f.places[pid].index_val >= 0:
                    (<list>self.use_of[f.places[pid].index_val]).append(i)
        for b in range(nb):
            tv = f.blocks[b].test_val
            if tv >= 0:
                (<list>self.use_of[tv]).append(-(b + 1))

    def __dealloc__(self):
        free(self.executable)
        free(self.reached)

    # -- lattice helpers ---------------------------------------------------

    cdef bint _set_lat(self, int32_t v, int32_t kind, double cval, object sfz):
        cdef int32_t ok = <int32_t>self.lk[v]
        if ok == kind:
            if kind == _LAT_CONST:
                if _dbits(<double>self.lc[v]) == _dbits(cval):
                    return False
            elif kind == _LAT_SET:
                if self.ls[v] == sfz:
                    return False
            else:
                return False
        self.lk[v] = kind
        self.lc[v] = cval
        self.ls[v] = sfz
        return True

    cdef _propagate(self, int32_t v):
        cdef int32_t x
        for x in <list>self.use_of[v]:
            if x >= 0:
                self.val_wl.append(x)
            else:
                self.test_wl.append(-x - 1)

    cdef bint _bool_arg(self, int32_t v):
        if <bint>self.is_bool[v]:
            return True
        if <int32_t>self.lk[v] == _LAT_CONST:
            if <double>self.lc[v] == 0.0 or <double>self.lc[v] == 1.0:
                return True
        return False

    cdef bint _and_zero(self, int32_t a0, int32_t a1):
        if <int32_t>self.lk[a0] == _LAT_CONST and <double>self.lc[a0] == 0.0 and self._bool_arg(a1):
            return True
        if <int32_t>self.lk[a1] == _LAT_CONST and <double>self.lc[a1] == 0.0 and self._bool_arg(a0):
            return True
        return False

    cdef bint _or_one(self, int32_t a0, int32_t a1):
        if <int32_t>self.lk[a0] == _LAT_CONST and <double>self.lc[a0] == 1.0 and self._bool_arg(a1):
            return True
        if <int32_t>self.lk[a1] == _LAT_CONST and <double>self.lc[a1] == 1.0 and self._bool_arg(a0):
            return True
        return False

    # -- transfer function for a non-phi value -----------------------------

    cdef object _transfer(self, int32_t i):
        cdef Func f = self.f
        cdef int32_t op = f.instrs[i].op
        cdef int32_t astart = f.instrs[i].arg_start
        cdef int32_t n = f.instrs[i].nargs
        cdef int32_t k, a, kk, a0, a1
        cdef bint saw_bottom, saw_top
        cdef double buf[16]
        cdef int32_t nc
        cdef double r
        cdef int status
        if op == OPX_CONST:
            return (_LAT_CONST, f.consts[f.instrs[i].aux], None)
        if op == OPX_UNDEF:
            # A read of a never-written scalar is a concrete unknown (the runtime
            # reads the shared never-written slot, e.g. -1.0), NOT optimistic TOP.
            # Treating it as BOTTOM keeps SCCP from folding a live phi(UNDEF, v)
            # merge to v; a *dead* UNDEF path is still pruned by edge executability
            # (its incoming edge is never marked), so the provably-dead collapse
            # the plan relies on is preserved.
            return (_LAT_BOTTOM, 0.0, None)
        if op == OPX_GET:
            return (_LAT_BOTTOM, 0.0, None)
        if op == OPX_SET:
            return None
        # --- policy exceptions (7.2.2), applied before the strict fold ---
        if op == OP_Multiply and n == 2:
            a0 = <int32_t>f.args[astart]
            a1 = <int32_t>f.args[astart + 1]
            if (<int32_t>self.lk[a0] == _LAT_CONST and <double>self.lc[a0] == 0.0) or \
               (<int32_t>self.lk[a1] == _LAT_CONST and <double>self.lc[a1] == 0.0):
                return (_LAT_CONST, 0.0, None)
        if op == OP_And and n == 2:
            if self._and_zero(<int32_t>f.args[astart], <int32_t>f.args[astart + 1]):
                return (_LAT_CONST, 0.0, None)
        if op == OP_Or and n == 2:
            if self._or_one(<int32_t>f.args[astart], <int32_t>f.args[astart + 1]):
                return (_LAT_CONST, 1.0, None)
        # --- strict value semantics over the argument lattice ---
        saw_bottom = False
        saw_top = False
        nc = 0
        if n > 16:
            return (_LAT_BOTTOM, 0.0, None)
        for k in range(n):
            a = <int32_t>f.args[astart + k]
            kk = <int32_t>self.lk[a]
            if kk == _LAT_BOTTOM or kk == _LAT_SET:
                saw_bottom = True
            elif kk == _LAT_TOP:
                saw_top = True
            else:
                buf[nc] = <double>self.lc[a]
                nc += 1
        if saw_bottom:
            return (_LAT_BOTTOM, 0.0, None)
        if saw_top:
            return (_LAT_TOP, 0.0, None)
        if op < OP_RUNTIME_COUNT and SONOLUS_OP_FOLDABLE[op] != 0:
            status = fold_op(<uint16_t>op, buf, n, &r)
            if status == FOLD_OK:
                return (_LAT_CONST, r, None)
            return (_LAT_BOTTOM, 0.0, None)
        return (_LAT_BOTTOM, 0.0, None)

    cdef _eval_and_propagate(self, int32_t v):
        cdef object r = self._transfer(v)
        if r is None:
            return
        if self._set_lat(v, <int32_t>r[0], <double>r[1], r[2]):
            self._propagate(v)

    # -- phi meet over executable incoming edges ---------------------------

    cdef _meet_phi(self, int32_t p):
        cdef Func f = self.f
        cdef int32_t b = f.instrs[p].block
        cdef int32_t astart = f.instrs[p].arg_start
        cdef list inc = <list>self.incoming[b]
        cdef int32_t k, e, a, kk, ne
        cdef bint saw_bottom = False
        elems = {}
        for k in range(len(inc)):
            e = <int32_t>inc[k]
            if not self.executable[e]:
                continue
            a = <int32_t>f.args[astart + k]
            kk = <int32_t>self.lk[a]
            if kk == _LAT_TOP:
                continue
            if kk == _LAT_BOTTOM:
                saw_bottom = True
                break
            if kk == _LAT_CONST:
                elems[_dbits(<double>self.lc[a])] = <double>self.lc[a]
            else:
                for bits in <object>self.ls[a]:
                    elems[bits] = _bits_to_d(<uint64_t><object>bits)
        if saw_bottom:
            if self._set_lat(p, _LAT_BOTTOM, 0.0, None):
                self._propagate(p)
            return
        ne = len(elems)
        if ne == 0:
            return
        if ne == 1:
            for dv in elems.values():
                if self._set_lat(p, _LAT_CONST, <double>dv, None):
                    self._propagate(p)
                break
        elif ne <= 100:
            if self._set_lat(p, _LAT_SET, 0.0, frozenset(elems.keys())):
                self._propagate(p)
        else:
            if self._set_lat(p, _LAT_BOTTOM, 0.0, None):
                self._propagate(p)

    # -- edge marking ------------------------------------------------------

    cdef _mark_edge(self, int32_t e):
        if not self.executable[e]:
            self.flow_wl.append(e)

    cdef int32_t _select_edge(self, int32_t b, double c):
        cdef Func f = self.f
        cdef int32_t es = f.blocks[b].edge_start
        cdef int32_t ec = f.blocks[b].edge_count
        cdef int32_t e, none_e = -1
        for e in range(es, es + ec):
            if f.edges[e].cond_kind == EDGE_COND_VALUE:
                if f.edges[e].cond == c:
                    return e
            else:
                none_e = e
        return none_e

    cdef _mark_out_edges(self, int32_t b):
        cdef Func f = self.f
        cdef int32_t es = f.blocks[b].edge_start
        cdef int32_t ec = f.blocks[b].edge_count
        cdef int32_t tv = f.blocks[b].test_val
        cdef int32_t e, kind, taken
        cdef double c
        if ec == 0:
            return
        if tv < 0:
            for e in range(es, es + ec):
                self._mark_edge(e)
            return
        kind = <int32_t>self.lk[tv]
        if kind == _LAT_BOTTOM:
            for e in range(es, es + ec):
                self._mark_edge(e)
            return
        if kind == _LAT_TOP:
            return
        if kind == _LAT_CONST:
            taken = self._select_edge(b, <double>self.lc[tv])
            if taken >= 0:
                self._mark_edge(taken)
            return
        for bits in <object>self.ls[tv]:
            c = _bits_to_d(<uint64_t><object>bits)
            taken = self._select_edge(b, c)
            if taken >= 0:
                self._mark_edge(taken)

    cdef _visit_block_first(self, int32_t b):
        cdef Func f = self.f
        cdef int32_t istart = f.blocks[b].instr_start
        cdef int32_t icount = f.blocks[b].instr_count
        cdef int32_t i
        for i in range(istart, istart + icount):
            if f.instrs[i].op == OPX_PHI:
                continue
            self._eval_and_propagate(i)
        self._mark_out_edges(b)

    cdef _drain(self):
        cdef Func f = self.f
        cdef int32_t e, d, v, b, op, p, pstart, pcount
        while self.flow_wl or self.val_wl or self.test_wl:
            while self.flow_wl:
                e = <int32_t>self.flow_wl.pop()
                if self.executable[e]:
                    continue
                self.executable[e] = 1
                d = f.edges[e].dst
                pstart = f.blocks[d].phi_start
                pcount = f.blocks[d].phi_count
                for p in range(pstart, pstart + pcount):
                    self._meet_phi(p)
                if not self.reached[d]:
                    self.reached[d] = 1
                    self._visit_block_first(d)
            while self.val_wl:
                v = <int32_t>self.val_wl.pop()
                op = f.instrs[v].op
                if op == OPX_PHI:
                    if self.reached[f.instrs[v].block]:
                        self._meet_phi(v)
                else:
                    if self.reached[f.instrs[v].block]:
                        self._eval_and_propagate(v)
            while self.test_wl:
                b = <int32_t>self.test_wl.pop()
                if self.reached[b]:
                    self._mark_out_edges(b)

    cdef _resolve_top_tests(self):
        # A reached block whose test never resolved (stays TOP) or is BOTTOM but
        # somehow unmarked takes ALL out-edges conservatively; a CONST/SET test
        # that legitimately selected nothing (default-less miss) stays an exit.
        cdef Func f = self.f
        cdef int32_t b, es, ec, e, tv, kind
        cdef bint added, any_exec
        while True:
            added = False
            for b in range(f.n_blocks):
                if not self.reached[b]:
                    continue
                ec = f.blocks[b].edge_count
                if ec == 0:
                    continue
                es = f.blocks[b].edge_start
                any_exec = False
                for e in range(es, es + ec):
                    if self.executable[e]:
                        any_exec = True
                        break
                if any_exec:
                    continue
                tv = f.blocks[b].test_val
                kind = <int32_t>self.lk[tv] if tv >= 0 else -1
                if tv < 0 or kind == _LAT_BOTTOM or kind == _LAT_TOP:
                    for e in range(es, es + ec):
                        self.flow_wl.append(e)
                    added = True
            if added:
                self._drain()
            else:
                break

    cdef run(self):
        cdef int32_t entry = self.f.entry_block
        self.reached[entry] = 1
        self._visit_block_first(entry)
        self._drain()
        self._resolve_top_tests()

    # -- iterative RPO over the surviving (executable) subgraph ------------

    def _rpo(self, list succ):
        # Iterative DFS postorder, reversed. Avoids negative list indexing (broken
        # under release-mode wraparound=False) via explicit parallel stacks.
        cdef Func f = self.f
        cdef int32_t entry = f.entry_block
        cdef int32_t node, idx, ch, depth
        visited = [False] * f.n_blocks
        order = []
        node_stack = [entry]
        idx_stack = [0]
        visited[entry] = True
        while node_stack:
            depth = len(node_stack) - 1
            node = <int32_t>node_stack[depth]
            idx = <int32_t>idx_stack[depth]
            slist = <list>succ[node]
            if idx < len(slist):
                idx_stack[depth] = idx + 1
                ch = <int32_t>slist[idx]
                if not <bint>visited[ch]:
                    visited[ch] = True
                    node_stack.append(ch)
                    idx_stack.append(0)
            else:
                order.append(node)
                node_stack.pop()
                idx_stack.pop()
        order.reverse()
        return order

    # -- build the rebuild decisions from the fixpoint ---------------------

    def decisions(self):
        cdef Func f = self.f
        cdef int32_t nb = f.n_blocks
        cdef int32_t ne = f.n_edges
        cdef int32_t e, b, bi, i, op, es, ec, tv, kind, si, mi
        cdef double cv
        keep_edge = [False] * ne
        for e in range(ne):
            if self.executable[e] and self.reached[f.edges[e].src] and self.reached[f.edges[e].dst]:
                keep_edge[e] = True
        # RPO over kept edges (deterministic: unconditional last, then by cond).
        succ = [[] for _ in range(nb)]
        for b in range(nb):
            if not self.reached[b]:
                continue
            es = f.blocks[b].edge_start
            ec = f.blocks[b].edge_count
            items = []
            for e in range(es, es + ec):
                if keep_edge[e]:
                    if f.edges[e].cond_kind == EDGE_COND_NONE:
                        items.append((1, 0.0, f.edges[e].dst))
                    else:
                        items.append((0, f.edges[e].cond, f.edges[e].dst))
            items.sort()
            succ[b] = [it[2] for it in items]
        order = self._rpo(succ)
        keep_instr = [False] * f.n_instrs
        for b in order:
            bi = <int32_t>b
            es = f.blocks[bi].instr_start
            ec = f.blocks[bi].instr_count
            for i in range(es, es + ec):
                keep_instr[i] = True
        # Const-valued value instrs become OPX_CONST (their uses see the const).
        const_override = {}
        for b in order:
            bi = <int32_t>b
            es = f.blocks[bi].instr_start
            ec = f.blocks[bi].instr_count
            for i in range(es, es + ec):
                if <int32_t>self.lk[i] != _LAT_CONST:
                    continue
                op = f.instrs[i].op
                if op == OPX_CONST or op == OPX_PHI or op == OPX_UNDEF or op == OPX_GET or op == OPX_SET:
                    continue
                if f.instrs[i].flags & FLAG_STMT_ROOT:
                    continue
                cv = <double>self.lc[i]
                if cv == 0.0 and signbit(cv):
                    # Do NOT materialize a folded -0.0 as a standalone constant.
                    # -0.0 is unrepresentable across the boundary: the Python
                    # IRConst cache collapses -0.0 -> +0.0 at construction (export /
                    # re-marshal), and emission int-demotes integral floats to int 0
                    # (finalize behaviour). A materialized -0.0 would therefore ship
                    # as +0.0, diverging from the *unfolded* path where the runtime
                    # computes e.g. 0 / -8 = -0.0 and keeps the sign -- a dual-run
                    # (debug-log) parity break. Leaving the instruction in place lets
                    # the real runtime fold it, preserving -0.0. The lattice value is
                    # still -0.0, so consumers that fold to a non-(-0.0) result are
                    # unaffected. (OPTIMIZER_REWRITE.md 7.2.2)
                    continue
                const_override[i] = cv
        changed = (len(order) < nb) or (len(const_override) > 0)
        for e in range(ne):
            if self.reached[f.edges[e].src] and not <bint>keep_edge[e]:
                changed = True
        # Per-block surviving-edge spec, with const-fold / switch-prune rewrites.
        out_spec = {}
        for b in order:
            bi = <int32_t>b
            es = f.blocks[bi].edge_start
            ec = f.blocks[bi].edge_count
            survivors = [e for e in range(es, es + ec) if <bint>keep_edge[e]]
            tv = f.blocks[bi].test_val
            kind = <int32_t>self.lk[tv] if tv >= 0 else -1
            spec = []
            if kind == _LAT_CONST:
                for e in survivors:
                    spec.append((f.edges[e].dst, EDGE_COND_NONE, 0.0, 0, e))
                    if f.edges[e].cond_kind != EDGE_COND_NONE:
                        changed = True
            elif kind == _LAT_SET:
                has_none = False
                for e in survivors:
                    if f.edges[e].cond_kind == EDGE_COND_NONE:
                        has_none = True
                    spec.append((f.edges[e].dst, f.edges[e].cond_kind, f.edges[e].cond,
                                 f.edges[e].cond_is_int, e))
                if not has_none and len(spec) > 0:
                    mi = -1
                    mc = None
                    for si in range(len(spec)):
                        if spec[si][1] == EDGE_COND_VALUE and (mc is None or spec[si][2] > mc):
                            mc = spec[si][2]
                            mi = si
                    if mi >= 0:
                        old = spec[mi]
                        spec[mi] = (old[0], EDGE_COND_NONE, 0.0, 0, old[4])
                        changed = True
            else:
                for e in survivors:
                    spec.append((f.edges[e].dst, f.edges[e].cond_kind, f.edges[e].cond,
                                 f.edges[e].cond_is_int, e))
            out_spec[bi] = spec
        return order, keep_instr, const_override, out_spec, changed


# --------------------------------------------------------------------------
# Shared arena rebuilder: emit a fresh compacted SSA Func from a kept-block RPO
# order, per-instr keep mask, const overrides, and per-block surviving-edge spec
# (5-tuples ``(dst_old, cond_kind, cond, cond_is_int, old_edge)``). Realigns phi
# operands to the surviving incoming edges in the new global-edge-index order.
# --------------------------------------------------------------------------

cdef int32_t _remap_place_c(Func dst, Func src, int32_t old_pid, dict newidx, dict place_map) except -1:
    cdef int32_t kind = src.places[old_pid].kind
    cdef int32_t flags = src.places[old_pid].flags
    cdef int32_t br = src.places[old_pid].block_ref
    cdef int32_t iv = src.places[old_pid].index_val
    cdef int32_t off = src.places[old_pid].offset
    if kind == PLACE_DYNAMIC_BLOCK:
        br = <int32_t>newidx[br]
    if iv >= 0:
        iv = <int32_t>newidx[iv]
    key = (kind, flags, br, iv, off)
    cached = place_map.get(key)
    if cached is not None:
        return <int32_t>cached
    cdef int32_t pid = _add_place(dst, <uint8_t>kind, <uint8_t>flags, br, iv, off)
    place_map[key] = pid
    return pid


def _build_compacted(Func src, list order, list keep_instr, dict const_override, dict out_spec):
    cdef Func dst = Func()
    cdef int32_t nb_old = src.n_blocks
    cdef int32_t nb_new = len(order)
    cdef int32_t i, b, bi, k, e, kk

    newblk = [-1] * nb_old
    for k in range(nb_new):
        newblk[<int32_t>order[k]] = k

    # consts / temps carry over 1:1 (ids preserved); override consts interned on top.
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
    dst.is_ssa = True

    override_cinfo = {}
    cdef double val
    cdef bint is_int
    cdef int32_t cid
    for i, oval in const_override.items():
        val = <double>oval
        cid = dst._intern_const(val)
        is_int = (not isinf(val)) and (not isnan(val)) and (val == floor(val))
        override_cinfo[i] = (cid, is_int)

    # phi operand maps keyed by old incoming edge.
    src_incoming = [[] for _ in range(nb_old)]
    for e in range(src.n_edges):
        src_incoming[src.edges[e].dst].append(e)
    phi_opmap = {}
    cdef int32_t pstart, pcount, p, astart
    for b in order:
        bi = <int32_t>b
        pstart = src.blocks[bi].phi_start
        pcount = src.blocks[bi].phi_count
        inclist = <list>src_incoming[bi]
        for p in range(pstart, pstart + pcount):
            astart = src.instrs[p].arg_start
            m = {}
            for k in range(src.instrs[p].nargs):
                m[<int32_t>inclist[k]] = <int32_t>src.args[astart + k]
            phi_opmap[p] = m

    # Emit new edges block-by-block; build per-old-block incoming (new order).
    new_edges = []
    incoming_new = [[] for _ in range(nb_old)]
    block_edge_start = [0] * nb_new
    block_edge_count = [0] * nb_new
    for k in range(nb_new):
        b = <int32_t>order[k]
        block_edge_start[k] = len(new_edges)
        for spec in out_spec.get(b, []):
            dst_old = <int32_t>spec[0]
            nei = len(new_edges)
            new_edges.append((k, newblk[dst_old], spec[1], spec[2], spec[3]))
            (<list>incoming_new[dst_old]).append((nei, spec[4]))
        block_edge_count[k] = len(new_edges) - block_edge_start[k]

    # Assign new instr ids + count args.
    newidx = {}
    block_instrs = [[] for _ in range(nb_new)]
    block_start = [0] * nb_new
    cdef int32_t next_idx = 0
    cdef int32_t total_args = 0
    cdef int32_t istart, icount, op
    for k in range(nb_new):
        b = <int32_t>order[k]
        block_start[k] = next_idx
        istart = src.blocks[b].instr_start
        icount = src.blocks[b].instr_count
        for i in range(istart, istart + icount):
            if not <bint>keep_instr[i]:
                continue
            (<list>block_instrs[k]).append(i)
            newidx[i] = next_idx
            next_idx += 1
            op = src.instrs[i].op
            if i in const_override:
                pass
            elif op == OPX_PHI:
                total_args += len(<list>incoming_new[b])
            elif op == OPX_CONST or op == OPX_UNDEF:
                pass
            else:
                total_args += src.instrs[i].nargs
    cdef int32_t total_instrs = next_idx

    dst.instrs = <Instr*>malloc(<size_t>(total_instrs if total_instrs > 0 else 1) * sizeof(Instr))
    dst.args = <uint32_t*>malloc(<size_t>(total_args if total_args > 0 else 1) * sizeof(uint32_t))
    if dst.instrs == NULL or dst.args == NULL:
        raise MemoryError()
    dst.n_instrs = total_instrs
    dst.cap_instrs = total_instrs
    dst.cap_args = total_args

    dst.blocks = <BlockInfo*>malloc(<size_t>(nb_new if nb_new > 0 else 1) * sizeof(BlockInfo))
    if dst.blocks == NULL:
        raise MemoryError()
    dst.n_blocks = nb_new
    dst.cap_blocks = nb_new
    dst.entry_block = newblk[src.entry_block]

    place_map = {}
    cdef int32_t arg_cursor = 0
    cdef int32_t ni, oldv, phi_first, phi_cnt, cnt, nn, val_new, pid, old_edge, operand
    cdef bint has_value_edge
    for k in range(nb_new):
        b = <int32_t>order[k]
        phi_first = -1
        phi_cnt = 0
        for oldv in <list>block_instrs[k]:
            ni = <int32_t>newidx[oldv]
            op = src.instrs[oldv].op
            dst.instrs[ni].block = k
            dst.instrs[ni].arg_start = arg_cursor
            if oldv in const_override:
                cid = <int32_t>override_cinfo[oldv][0]
                is_int = <bint>override_cinfo[oldv][1]
                dst.instrs[ni].op = OPX_CONST
                dst.instrs[ni].flags = <uint8_t>(FLAG_PURE | (FLAG_CONST_IS_INT if is_int else 0))
                dst.instrs[ni].aux = cid
                dst.instrs[ni].nargs = 0
            elif op == OPX_PHI:
                if phi_first == -1:
                    phi_first = ni
                phi_cnt += 1
                m = <dict>phi_opmap[oldv]
                cnt = 0
                for pair in <list>incoming_new[b]:
                    old_edge = <int32_t>pair[1]
                    operand = <int32_t>m[old_edge]
                    dst.args[arg_cursor] = <uint32_t><int32_t>newidx[operand]
                    arg_cursor += 1
                    cnt += 1
                dst.instrs[ni].op = OPX_PHI
                dst.instrs[ni].flags = src.instrs[oldv].flags
                dst.instrs[ni].aux = src.instrs[oldv].aux
                dst.instrs[ni].nargs = <int16_t>cnt
            elif op == OPX_CONST:
                dst.instrs[ni].op = OPX_CONST
                dst.instrs[ni].flags = src.instrs[oldv].flags
                dst.instrs[ni].aux = src.instrs[oldv].aux
                dst.instrs[ni].nargs = 0
            elif op == OPX_UNDEF:
                dst.instrs[ni].op = OPX_UNDEF
                dst.instrs[ni].flags = src.instrs[oldv].flags
                dst.instrs[ni].aux = -1
                dst.instrs[ni].nargs = 0
            elif op == OPX_GET:
                pid = _remap_place_c(dst, src, src.instrs[oldv].aux, newidx, place_map)
                dst.instrs[ni].op = OPX_GET
                dst.instrs[ni].flags = src.instrs[oldv].flags
                dst.instrs[ni].aux = pid
                dst.instrs[ni].nargs = 0
            elif op == OPX_SET:
                val_new = <int32_t>newidx[<int32_t>src.args[src.instrs[oldv].arg_start]]
                pid = _remap_place_c(dst, src, src.instrs[oldv].aux, newidx, place_map)
                dst.args[arg_cursor] = <uint32_t>val_new
                arg_cursor += 1
                dst.instrs[ni].op = OPX_SET
                dst.instrs[ni].flags = src.instrs[oldv].flags
                dst.instrs[ni].aux = pid
                dst.instrs[ni].nargs = 1
            else:
                astart = src.instrs[oldv].arg_start
                nn = src.instrs[oldv].nargs
                for kk in range(nn):
                    dst.args[arg_cursor] = <uint32_t><int32_t>newidx[<int32_t>src.args[astart + kk]]
                    arg_cursor += 1
                dst.instrs[ni].op = <uint16_t>op
                dst.instrs[ni].flags = src.instrs[oldv].flags
                dst.instrs[ni].aux = src.instrs[oldv].aux
                dst.instrs[ni].nargs = <int16_t>nn
        dst.blocks[k].instr_start = <int32_t>block_start[k]
        dst.blocks[k].instr_count = len(<list>block_instrs[k])
        dst.blocks[k].phi_start = phi_first if phi_cnt > 0 else <int32_t>block_start[k]
        dst.blocks[k].phi_count = phi_cnt
        dst.blocks[k].rpo = k
        dst.blocks[k].idom = -1
        dst.blocks[k].edge_start = <int32_t>block_edge_start[k]
        dst.blocks[k].edge_count = <int32_t>block_edge_count[k]
        has_value_edge = False
        for spec in out_spec.get(b, []):
            if spec[1] == EDGE_COND_VALUE:
                has_value_edge = True
                break
        if has_value_edge and src.blocks[b].test_val >= 0:
            dst.blocks[k].test_val = <int32_t>newidx[src.blocks[b].test_val]
        else:
            dst.blocks[k].test_val = -1
    dst.n_args = arg_cursor

    # Edges array.
    cdef int32_t nen = len(new_edges)
    dst.edges = <Edge*>malloc(<size_t>(nen if nen > 0 else 1) * sizeof(Edge))
    if dst.edges == NULL:
        raise MemoryError()
    for e in range(nen):
        ne_t = new_edges[e]
        dst.edges[e].src = <int32_t>ne_t[0]
        dst.edges[e].dst = <int32_t>ne_t[1]
        dst.edges[e].cond_kind = <uint8_t>ne_t[2]
        dst.edges[e].cond = <double>ne_t[3]
        dst.edges[e].cond_is_int = <uint8_t>ne_t[4]
    dst.n_edges = nen
    dst.cap_edges = nen

    if src.undef_val >= 0 and <bint>keep_instr[src.undef_val]:
        dst.undef_val = <int32_t>newidx[src.undef_val]
    else:
        dst.undef_val = -1
    widened = set()
    if src._ssa_undef:
        for wv in src._ssa_undef:
            if <bint>keep_instr[wv]:
                widened.add(<int32_t>newidx[wv])
    dst._ssa_undef = widened

    compute_dominators(dst)
    return dst


# --------------------------------------------------------------------------
# GVN -- dominator-scoped hash value numbering + algebraic identities (7.2.3).
# Rewrites operands in place (uses -> dominating canonical value); the redundant
# defs become unused and DCE reclaims them.
# --------------------------------------------------------------------------

cdef int32_t _resolve(dict subst, int32_t v):
    while v in subst:
        v = <int32_t>subst[v]
    return v


cdef bint _is_c(Func f, int32_t v, double c):
    return f.instrs[v].op == OPX_CONST and f.consts[f.instrs[v].aux] == c


def _gvn_instr(Func f, int32_t i, dict avail, list undo, dict subst, list is_bool, object widened, list changed):
    cdef int32_t op = f.instrs[i].op
    cdef int32_t astart = f.instrs[i].arg_start
    cdef int32_t n = f.instrs[i].nargs
    cdef int32_t pid, iv, ivr, inner, k, av, innerarg, neg_inner
    if i in widened:
        return
    if op == OPX_UNDEF or op == OPX_SET or op == OPX_PHI:
        return
    if op == OPX_CONST:
        key = ("c", f.instrs[i].aux)
        prev = avail.get(key)
        if prev is not None:
            subst[i] = <int32_t>prev
            changed[0] = True
            return
        undo.append((key, _MISSING))
        avail[key] = i
        return
    if op == OPX_GET:
        pid = f.instrs[i].aux
        if f.places[pid].kind == PLACE_REAL_BLOCK and not (f.places[pid].flags & PLACE_WRITABLE):
            iv = f.places[pid].index_val
            if iv >= 0:
                ivr = _resolve(subst, iv)
                if ivr in widened:
                    return
            else:
                ivr = -1
            key = ("g", f.places[pid].block_ref, ivr, f.places[pid].offset)
            prev = avail.get(key)
            if prev is not None:
                subst[i] = <int32_t>prev
                changed[0] = True
                return
            undo.append((key, _MISSING))
            avail[key] = i
        return
    if not (f.instrs[i].flags & FLAG_PURE):
        return
    # resolve operands (skip GVN if any operand is a dominance-relaxed UNDEF value).
    a = []
    for k in range(n):
        av = _resolve(subst, <int32_t>f.args[astart + k])
        if av in widened:
            return
        a.append(av)
    # algebraic identities (binary/unary forms; 7.2.3 / section 4).
    if op == OP_Add and n == 2:
        if _is_c(f, <int32_t>a[1], 0.0):
            subst[i] = <int32_t>a[0]; changed[0] = True; return
        if _is_c(f, <int32_t>a[0], 0.0):
            subst[i] = <int32_t>a[1]; changed[0] = True; return
        if f.instrs[<int32_t>a[1]].op == OP_Negate and f.instrs[<int32_t>a[1]].nargs == 1:
            # x + (-y) -> x - y  (bit-exact IEEE: a + (-y) == a - y). Only the
            # trailing arg (args[1]); the Add spine's FP order is preserved.
            neg_inner = _resolve(subst, <int32_t>f.args[f.instrs[<int32_t>a[1]].arg_start])
            f.instrs[i].op = OP_Subtract
            f.args[astart + 1] = <uint32_t>neg_inner
            op = OP_Subtract
            a[1] = neg_inner
            changed[0] = True
    elif op == OP_Subtract and n == 2:
        if _is_c(f, <int32_t>a[1], 0.0):
            subst[i] = <int32_t>a[0]; changed[0] = True; return
        if _is_c(f, <int32_t>a[0], 0.0):
            # 0 - x -> Negate(x)  (documented -0.0 tolerance, matches old)
            f.instrs[i].op = OP_Negate
            f.args[astart] = <uint32_t><int32_t>a[1]
            f.instrs[i].nargs = 1
            op = OP_Negate
            n = 1
            a = [a[1]]
            changed[0] = True
        elif f.instrs[<int32_t>a[1]].op == OP_Negate and f.instrs[<int32_t>a[1]].nargs == 1:
            # x - (-y) -> x + y  (bit-exact IEEE: a - (-y) == a + y).
            neg_inner = _resolve(subst, <int32_t>f.args[f.instrs[<int32_t>a[1]].arg_start])
            f.instrs[i].op = OP_Add
            f.args[astart + 1] = <uint32_t>neg_inner
            op = OP_Add
            a[1] = neg_inner
            changed[0] = True
    elif op == OP_Multiply and n == 2:
        if _is_c(f, <int32_t>a[1], 1.0):
            subst[i] = <int32_t>a[0]; changed[0] = True; return
        if _is_c(f, <int32_t>a[0], 1.0):
            subst[i] = <int32_t>a[1]; changed[0] = True; return
    elif op == OP_Divide and n == 2:
        if _is_c(f, <int32_t>a[1], 1.0):
            subst[i] = <int32_t>a[0]; changed[0] = True; return
    if op == OP_Negate and n == 1:
        inner = <int32_t>a[0]
        if f.instrs[inner].op == OP_Negate:
            subst[i] = _resolve(subst, <int32_t>f.args[f.instrs[inner].arg_start])
            changed[0] = True
            return
    if op == OP_Not and n == 1:
        inner = <int32_t>a[0]
        if f.instrs[inner].op == OP_Not:
            innerarg = _resolve(subst, <int32_t>f.args[f.instrs[inner].arg_start])
            if <bint>is_bool[innerarg]:
                subst[i] = innerarg
                changed[0] = True
                return
    if (op == OP_Min or op == OP_Max) and n == 2 and a[0] == a[1]:
        subst[i] = <int32_t>a[0]; changed[0] = True; return
    # commutative canonicalization by value id (Equal/NotEqual/Max/Min only).
    if (op == OP_Equal or op == OP_NotEqual or op == OP_Max or op == OP_Min) and n == 2 and a[0] > a[1]:
        tmp = a[0]; a[0] = a[1]; a[1] = tmp
        changed[0] = True
    # persist resolved + canonical operands.
    for k in range(n):
        f.args[astart + k] = <uint32_t><int32_t>a[k]
    key = ("o", op, tuple(a))
    prev = avail.get(key)
    if prev is not None:
        subst[i] = <int32_t>prev
        changed[0] = True
        return
    undo.append((key, _MISSING))
    avail[key] = i


def _apply_subst(Func f, dict subst):
    cdef int32_t i, b, k, astart, nargs, tv, pid
    for i in range(f.n_instrs):
        astart = f.instrs[i].arg_start
        nargs = f.instrs[i].nargs
        for k in range(nargs):
            f.args[astart + k] = <uint32_t>_resolve(subst, <int32_t>f.args[astart + k])
    for b in range(f.n_blocks):
        tv = f.blocks[b].test_val
        if tv >= 0:
            f.blocks[b].test_val = _resolve(subst, tv)
    for pid in range(f.n_places):
        if f.places[pid].kind == PLACE_DYNAMIC_BLOCK:
            f.places[pid].block_ref = _resolve(subst, f.places[pid].block_ref)
        if f.places[pid].index_val >= 0:
            f.places[pid].index_val = _resolve(subst, f.places[pid].index_val)


def _collapse_trivial_phis(Func f):
    """Collapse single-operand phis (pure copies left by SCCP edge pruning).

    When SCCP prunes a block's dead incoming edges, its phis realign to the
    surviving edges; a phi reduced to one operand is just a copy of that operand
    (which, coming from the sole predecessor, dominates the block). Substitute it
    away in place; DCE then drops the now-unused phi. Chains (a 1-phi feeding a
    1-phi) resolve through the subst map.
    """
    cdef int32_t b, ps, pc, p
    subst = {}
    for b in range(f.n_blocks):
        ps = f.blocks[b].phi_start
        pc = f.blocks[b].phi_count
        for p in range(ps, ps + pc):
            if f.instrs[p].nargs == 1:
                subst[p] = <int32_t>f.args[f.instrs[p].arg_start]
    if subst:
        _apply_subst(f, subst)
        return (f, True)
    return (f, False)


def _run_gvn_inplace(Func f):
    cdef Dominators D = compute_dominators(f)
    is_bool = _compute_bool(f)
    widened = f._ssa_undef if f._ssa_undef is not None else set()
    subst = {}
    avail = {}
    changed = [False]
    cdef int32_t entry = f.entry_block
    cdef int32_t b, i, istart, icount, ci, ch_start, ch_end
    work = [("enter", entry)]
    while work:
        frame = work.pop()
        if frame[0] == "exit":
            for pair in <list>frame[1]:
                key = pair[0]
                if pair[1] is _MISSING:
                    del avail[key]
                else:
                    avail[key] = pair[1]
            continue
        b = <int32_t>frame[1]
        undo = []
        istart = f.blocks[b].instr_start
        icount = f.blocks[b].instr_count
        for i in range(istart, istart + icount):
            if f.instrs[i].op == OPX_PHI:
                continue
            _gvn_instr(f, i, avail, undo, subst, is_bool, widened, changed)
        work.append(("exit", undo))
        ch_start = D.child_head[b]
        ch_end = D.child_head[b + 1]
        for ci in range(ch_end - 1, ch_start - 1, -1):
            work.append(("enter", D.child_list[ci]))
    if subst or <bint>changed[0]:
        _apply_subst(f, subst)
        return (f, True)
    return (f, False)


# --------------------------------------------------------------------------
# Two-way branch canonicalization: If(Not(x)) -> swap edges, drop the Not (7.2 /
# survey finding #2). Runs in the shared mid-end pass (fast + standard), after
# GVN's _apply_subst (so tests/operands are resolved) and before DCE (so a freed
# Not is reaped). Pure edge relabel on the arena; no instrs move.
# --------------------------------------------------------------------------

def _canon_branch_not(Func f):
    """Rewrite a two-way ``{VALUE 0, NONE}`` block whose test is ``Not(x)``.

    Emit lowers such a block to ``If(test, none_target, zero_target)``. Since
    ``Not(x)`` is nonzero iff ``x == 0``, ``If(Not(x), a, b) == If(x, b, a)`` --
    so peeling one ``Not`` swaps the ``cond=0`` and ``NONE`` edge labels while
    setting the test to the inner value; ``Not(Not(x))`` reduces to ``x`` with no
    swap (a block test only distinguishes zero/nonzero, so double-Not is a no-op
    regardless of boolean-ness). Behaviour-preserving including NaN: ``Not(NaN)=0``
    took the false branch; after the swap ``NaN != 0`` takes the true edge, which
    now targets the original false block. Only two-way ``{VALUE 0, NONE}`` blocks
    (multiway case conds compare against the test value, so swapping does not
    apply). The dead ``Not`` instrs are left orphaned for DCE.
    """
    cdef int32_t nb = f.n_blocks
    cdef Edge* edges = f.edges
    cdef int32_t b, e, estart, ecount, tv, val_e, none_e, count
    cdef bint changed = False
    for b in range(nb):
        ecount = f.blocks[b].edge_count
        if ecount != 2:
            continue
        tv = f.blocks[b].test_val
        if tv < 0:
            continue
        estart = f.blocks[b].edge_start
        val_e = -1
        none_e = -1
        for e in range(estart, estart + ecount):
            if edges[e].cond_kind == EDGE_COND_NONE:
                none_e = e
            elif edges[e].cond_kind == EDGE_COND_VALUE and edges[e].cond == 0.0:
                val_e = e
        if val_e < 0 or none_e < 0:
            continue
        count = 0
        while f.instrs[tv].op == OP_Not and f.instrs[tv].nargs == 1:
            tv = <int32_t>f.args[f.instrs[tv].arg_start]
            count += 1
        if count == 0:
            continue
        f.blocks[b].test_val = tv
        if count % 2 == 1:
            edges[val_e].cond_kind = EDGE_COND_NONE
            edges[val_e].cond = 0.0
            edges[val_e].cond_is_int = 0
            edges[none_e].cond_kind = EDGE_COND_VALUE
            edges[none_e].cond = 0.0
            edges[none_e].cond_is_int = 1
        changed = True
    return changed


# --------------------------------------------------------------------------
# DCE -- worklist mark from roots (block tests + FLAG_STMT_ROOT stores/effects),
# then compact away everything unmarked (7.2.4).
# --------------------------------------------------------------------------

def _run_dce(Func f):
    cdef int32_t n = f.n_instrs
    live = [False] * n
    wl = []
    cdef int32_t i, b, tv, op, astart, nargs, k, a, pid, br, ivv, v, e, es, ec
    for b in range(f.n_blocks):
        tv = f.blocks[b].test_val
        if tv >= 0 and not <bint>live[tv]:
            live[tv] = True
            wl.append(tv)
    for i in range(n):
        # Roots: bare statement roots AND every side-effecting instruction (7.2.4 --
        # side effects are never deletable). A side-effecting value can lack
        # FLAG_STMT_ROOT when its size-1 scalar store dissolved during SSA promotion
        # (e.g. ``x <- DebugLog(...)`` where ``x`` is unread): the effect must still
        # persist. FLAG_SIDE_EFFECT excludes ``Random`` (side_effects=False), which
        # stays deletable-when-unused, matching lower.pyx's materialize logic.
        if f.instrs[i].flags & (FLAG_STMT_ROOT | FLAG_SIDE_EFFECT):
            if not <bint>live[i]:
                live[i] = True
                wl.append(i)
    while wl:
        v = <int32_t>wl.pop()
        op = f.instrs[v].op
        if op == OPX_CONST or op == OPX_UNDEF:
            continue
        astart = f.instrs[v].arg_start
        nargs = f.instrs[v].nargs
        for k in range(nargs):
            a = <int32_t>f.args[astart + k]
            if not <bint>live[a]:
                live[a] = True
                wl.append(a)
        if op == OPX_GET or op == OPX_SET:
            pid = f.instrs[v].aux
            if f.places[pid].kind == PLACE_DYNAMIC_BLOCK:
                br = f.places[pid].block_ref
                if not <bint>live[br]:
                    live[br] = True
                    wl.append(br)
            ivv = f.places[pid].index_val
            if ivv >= 0 and not <bint>live[ivv]:
                live[ivv] = True
                wl.append(ivv)
    changed = False
    for i in range(n):
        if not <bint>live[i]:
            changed = True
            break
    if not changed:
        return (f, False)
    order = list(range(f.n_blocks))
    out_spec = {}
    for b in range(f.n_blocks):
        es = f.blocks[b].edge_start
        ec = f.blocks[b].edge_count
        spec = []
        for e in range(es, es + ec):
            spec.append((f.edges[e].dst, f.edges[e].cond_kind, f.edges[e].cond, f.edges[e].cond_is_int, e))
        out_spec[b] = spec
    newf = _build_compacted(f, order, live, {}, out_spec)
    return (newf, True)


# ==========================================================================
# LICM (7.2.5) and rewrite_switch (7.2.6) -- M3 standard-level SSA passes.
#
# Both reshape the CFG on SSA form (phis live). ``rewrite_switch`` only rewrites
# block tests + edge conds/targets and drops dead blocks (no instr relocation),
# so it reuses the SCCP/DCE arena rebuilder (``_build_compacted``) after mutating
# tests in place. LICM relocates instructions into a fresh preheader block, which
# ``_build_compacted`` cannot express, so it uses the general model-driven
# rebuilder ``_emit_from_model`` below.
# ==========================================================================


# ---- general model-driven SSA arena rebuilder ----------------------------
# A "model" is a list of plan-blocks (``pblocks``; arbitrary order, entry given).
# Each plan-block is a dict:
#   "items": value items in schedule order (phis first):
#       ("i",  old_vid, opmap|None)   copy src instr old_vid. If it is an OPX_PHI,
#                                     ``opmap`` {edge_key: vref} gives per-edge
#                                     operands; otherwise opmap is None (operands
#                                     copied from src and remapped).
#       ("np", token, temp_id, opmap) a brand-new phi ({edge_key: vref}).
#   "test":  vref | None
#   "edges": list of [dst_pb, cond_kind, cond, cond_is_int, edge_key]
# vref = ("o", old_vid) | ("n", token). ``edge_key`` is any hashable, matched
# between an incoming edge spec and a phi's opmap. Produces a fresh SSA ``Func``.

cdef int32_t _resolve_vref(object ref, dict oldmap, dict tokenmap) except -1:
    if <str>ref[0] == "o":
        return <int32_t>oldmap[<int32_t>ref[1]]
    return <int32_t>tokenmap[ref[1]]


def _emit_from_model(Func src, list pblocks, int entry_pb):
    cdef Func dst = Func()
    cdef int32_t npb = len(pblocks)
    cdef int32_t i, k, pb, kk, nn, astart, ov, op, pid, val_new, ni, arg_cursor
    cdef int32_t total_instrs, total_args, nb_new, ninc

    # --- reachability + RPO from entry over model edges ---
    succ = [[] for _ in range(npb)]
    for pb in range(npb):
        for espec in <list>(<dict>pblocks[pb])["edges"]:
            (<list>succ[pb]).append(<int32_t>espec[0])
    visited = [False] * npb
    post = []
    stack = [(entry_pb, 0)]
    visited[entry_pb] = True
    while stack:
        top = stack[len(stack) - 1]
        node = <int32_t>top[0]
        idx = <int32_t>top[1]
        slist = <list>succ[node]
        if idx < len(slist):
            stack[len(stack) - 1] = (node, idx + 1)
            ch = <int32_t>slist[idx]
            if not <bint>visited[ch]:
                visited[ch] = True
                stack.append((ch, 0))
        else:
            post.append(node)
            stack.pop()
    rpo = list(reversed(post))
    nb_new = len(rpo)
    new_bid = {}
    for k in range(nb_new):
        new_bid[<int32_t>rpo[k]] = k

    # --- consts / temps / names carry over 1:1 (ids preserved) ---
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
    dst.is_ssa = True

    # --- edges (block-by-block in RPO) + per-dst incoming (new-edge-index order) ---
    new_edges = []
    incoming_new = [[] for _ in range(nb_new)]
    block_edge_start = [0] * nb_new
    block_edge_count = [0] * nb_new
    for k in range(nb_new):
        pb = <int32_t>rpo[k]
        block_edge_start[k] = len(new_edges)
        for espec in <list>(<dict>pblocks[pb])["edges"]:
            dst_pb = <int32_t>espec[0]
            if dst_pb not in new_bid:
                continue
            nb_dst = <int32_t>new_bid[dst_pb]
            nei = len(new_edges)
            new_edges.append((k, nb_dst, <int32_t>espec[1], <double>espec[2], <int32_t>espec[3]))
            (<list>incoming_new[nb_dst]).append((nei, espec[4]))
        block_edge_count[k] = len(new_edges) - block_edge_start[k]

    # --- assign new value ids (phis first per block already, by item order) ---
    oldmap = {}
    tokenmap = {}
    block_items = [None] * nb_new
    block_start = [0] * nb_new
    ni = 0
    for k in range(nb_new):
        pb = <int32_t>rpo[k]
        items = <list>(<dict>pblocks[pb])["items"]
        block_items[k] = items
        block_start[k] = ni
        for it in items:
            if <str>it[0] == "i":
                oldmap[<int32_t>it[1]] = ni
            else:
                tokenmap[it[1]] = ni
            ni += 1
    total_instrs = ni

    # --- count operand slots ---
    total_args = 0
    for k in range(nb_new):
        ninc = len(<list>incoming_new[k])
        for it in <list>block_items[k]:
            if <str>it[0] == "np":
                total_args += ninc
            else:
                ov = <int32_t>it[1]
                op = src.instrs[ov].op
                if op == OPX_PHI:
                    total_args += ninc
                elif op == OPX_CONST or op == OPX_UNDEF or op == OPX_GET:
                    pass
                elif op == OPX_SET:
                    total_args += 1
                else:
                    total_args += src.instrs[ov].nargs

    dst.instrs = <Instr*>malloc(<size_t>(total_instrs if total_instrs > 0 else 1) * sizeof(Instr))
    dst.args = <uint32_t*>malloc(<size_t>(total_args if total_args > 0 else 1) * sizeof(uint32_t))
    if dst.instrs == NULL or dst.args == NULL:
        raise MemoryError()
    dst.n_instrs = total_instrs
    dst.cap_instrs = total_instrs
    dst.cap_args = total_args

    dst.blocks = <BlockInfo*>malloc(<size_t>(nb_new if nb_new > 0 else 1) * sizeof(BlockInfo))
    if dst.blocks == NULL:
        raise MemoryError()
    dst.n_blocks = nb_new
    dst.cap_blocks = nb_new
    dst.entry_block = <int32_t>new_bid[entry_pb]

    place_map = {}
    arg_cursor = 0
    cdef int32_t phi_first, phi_cnt
    for k in range(nb_new):
        pb = <int32_t>rpo[k]
        inclist = <list>incoming_new[k]
        ninc = len(inclist)
        phi_first = -1
        phi_cnt = 0
        ni = <int32_t>block_start[k]
        for it in <list>block_items[k]:
            dst.instrs[ni].block = k
            dst.instrs[ni].arg_start = arg_cursor
            if <str>it[0] == "np":
                opmap = <dict>it[3]
                for pair in inclist:
                    dst.args[arg_cursor] = <uint32_t>_resolve_vref(opmap[pair[1]], oldmap, tokenmap)
                    arg_cursor += 1
                dst.instrs[ni].op = OPX_PHI
                dst.instrs[ni].flags = 0
                dst.instrs[ni].aux = <int32_t>it[2]
                dst.instrs[ni].nargs = <int16_t>ninc
                if phi_first == -1:
                    phi_first = ni
                phi_cnt += 1
            else:
                ov = <int32_t>it[1]
                op = src.instrs[ov].op
                if op == OPX_PHI:
                    opmap = <dict>it[2]
                    for pair in inclist:
                        dst.args[arg_cursor] = <uint32_t>_resolve_vref(opmap[pair[1]], oldmap, tokenmap)
                        arg_cursor += 1
                    dst.instrs[ni].op = OPX_PHI
                    dst.instrs[ni].flags = src.instrs[ov].flags
                    dst.instrs[ni].aux = src.instrs[ov].aux
                    dst.instrs[ni].nargs = <int16_t>ninc
                    if phi_first == -1:
                        phi_first = ni
                    phi_cnt += 1
                elif op == OPX_CONST:
                    dst.instrs[ni].op = OPX_CONST
                    dst.instrs[ni].flags = src.instrs[ov].flags
                    dst.instrs[ni].aux = src.instrs[ov].aux
                    dst.instrs[ni].nargs = 0
                elif op == OPX_UNDEF:
                    dst.instrs[ni].op = OPX_UNDEF
                    dst.instrs[ni].flags = src.instrs[ov].flags
                    dst.instrs[ni].aux = -1
                    dst.instrs[ni].nargs = 0
                elif op == OPX_GET:
                    pid = _remap_place_c(dst, src, src.instrs[ov].aux, oldmap, place_map)
                    dst.instrs[ni].op = OPX_GET
                    dst.instrs[ni].flags = src.instrs[ov].flags
                    dst.instrs[ni].aux = pid
                    dst.instrs[ni].nargs = 0
                elif op == OPX_SET:
                    val_new = <int32_t>oldmap[<int32_t>src.args[src.instrs[ov].arg_start]]
                    pid = _remap_place_c(dst, src, src.instrs[ov].aux, oldmap, place_map)
                    dst.args[arg_cursor] = <uint32_t>val_new
                    arg_cursor += 1
                    dst.instrs[ni].op = OPX_SET
                    dst.instrs[ni].flags = src.instrs[ov].flags
                    dst.instrs[ni].aux = pid
                    dst.instrs[ni].nargs = 1
                else:
                    astart = src.instrs[ov].arg_start
                    nn = src.instrs[ov].nargs
                    for kk in range(nn):
                        dst.args[arg_cursor] = <uint32_t><int32_t>oldmap[<int32_t>src.args[astart + kk]]
                        arg_cursor += 1
                    dst.instrs[ni].op = <uint16_t>op
                    dst.instrs[ni].flags = src.instrs[ov].flags
                    dst.instrs[ni].aux = src.instrs[ov].aux
                    dst.instrs[ni].nargs = <int16_t>nn
            ni += 1
        dst.blocks[k].instr_start = <int32_t>block_start[k]
        dst.blocks[k].instr_count = len(<list>block_items[k])
        dst.blocks[k].phi_start = phi_first if phi_cnt > 0 else <int32_t>block_start[k]
        dst.blocks[k].phi_count = phi_cnt
        dst.blocks[k].rpo = k
        dst.blocks[k].idom = -1
        dst.blocks[k].edge_start = <int32_t>block_edge_start[k]
        dst.blocks[k].edge_count = <int32_t>block_edge_count[k]
        tref = (<dict>pblocks[pb])["test"]
        has_value_edge = False
        for espec in <list>(<dict>pblocks[pb])["edges"]:
            if <int32_t>espec[0] in new_bid and <int32_t>espec[1] == EDGE_COND_VALUE:
                has_value_edge = True
                break
        if tref is not None and has_value_edge:
            dst.blocks[k].test_val = _resolve_vref(tref, oldmap, tokenmap)
        else:
            dst.blocks[k].test_val = -1
    dst.n_args = arg_cursor

    cdef int32_t nen = len(new_edges)
    dst.edges = <Edge*>malloc(<size_t>(nen if nen > 0 else 1) * sizeof(Edge))
    if dst.edges == NULL:
        raise MemoryError()
    for i in range(nen):
        ne_t = new_edges[i]
        dst.edges[i].src = <int32_t>ne_t[0]
        dst.edges[i].dst = <int32_t>ne_t[1]
        dst.edges[i].cond_kind = <uint8_t>ne_t[2]
        dst.edges[i].cond = <double>ne_t[3]
        dst.edges[i].cond_is_int = <uint8_t>ne_t[4]
    dst.n_edges = nen
    dst.cap_edges = nen

    if src.undef_val >= 0 and src.undef_val in oldmap:
        dst.undef_val = <int32_t>oldmap[src.undef_val]
    else:
        dst.undef_val = -1
    widened = set()
    if src._ssa_undef:
        for wv in src._ssa_undef:
            if wv in oldmap:
                widened.add(<int32_t>oldmap[wv])
    dst._ssa_undef = widened

    compute_dominators(dst)
    return dst


# ---- LICM (7.2.5) --------------------------------------------------------
# Loop forest from dominators + back edges. For each loop (inner-first), hoist
# pure / effectively-pure (non-writable static real-block reads), loop-invariant,
# guaranteed-to-execute (def block dominates every latch) values whose EFFECTIVE
# cost (section 2) is >= 4 into a preheader. Effective cost: runtime-constant
# subtrees (pure ops over OPX_CONST + PLACE_RUNTIME_CONST reads) cost 1, so they
# NEVER hoist -- deliberately diverging from the old LICM, which hoisted them and
# blocked the runtime's own constant folding (OPTIMIZER_REWRITE.md 7.2.5). This
# effective-cost walk duplicates the one in lower.pyx (a cimport would create a
# midend<->lower cycle); the two stay aligned with section 2.


cdef bint _licm_is_rtc(Func f, int32_t v, dict memo) except -1:
    # True iff the whole subtree rooted at v is runtime-constant.
    cached = memo.get(v)
    if cached is not None:
        return <bint>cached
    cdef uint16_t op = f.instrs[v].op
    cdef int32_t astart, n, k, pid
    cdef bint r = False
    if op == OPX_CONST:
        r = True
    elif op == OPX_GET:
        pid = f.instrs[v].aux
        r = (f.places[pid].flags & PLACE_RUNTIME_CONST) != 0
    elif op < OP_RUNTIME_COUNT and (f.instrs[v].flags & FLAG_PURE):
        r = True
        astart = f.instrs[v].arg_start
        n = f.instrs[v].nargs
        for k in range(n):
            if not _licm_is_rtc(f, <int32_t>f.args[astart + k], memo):
                r = False
                break
    memo[v] = r
    return r


cdef int32_t _licm_eff_cost(Func f, int32_t v, dict memo, dict memo_rtc) except -1:
    # Effective cost (section 2), mirroring the old CSE/LICM _cost tree walk with
    # the runtime-constant refinement. Memoised per value id.
    cached = memo.get(v)
    if cached is not None:
        return <int32_t>cached
    if _licm_is_rtc(f, v, memo_rtc):
        memo[v] = 1
        return 1
    cdef uint16_t op = f.instrs[v].op
    cdef int32_t astart, n, k, pid, iv, c
    if op == OPX_CONST:
        c = 1
    elif op == OPX_UNDEF:
        c = 3
    elif op == OPX_GET:
        pid = f.instrs[v].aux
        c = 1
        if f.places[pid].kind == PLACE_DYNAMIC_BLOCK:
            c += _licm_eff_cost(f, f.places[pid].block_ref, memo, memo_rtc)
        else:
            c += 1
        iv = f.places[pid].index_val
        if iv < 0:
            c += 1
        else:
            c += _licm_eff_cost(f, iv, memo, memo_rtc)
    elif op < OP_RUNTIME_COUNT and (f.instrs[v].flags & FLAG_PURE):
        c = 1
        astart = f.instrs[v].arg_start
        n = f.instrs[v].nargs
        for k in range(n):
            c += _licm_eff_cost(f, <int32_t>f.args[astart + k], memo, memo_rtc)
    else:
        # phi / impure / Random -- a materialised scalar value reference.
        c = 3
    memo[v] = c
    return c


cdef bint _licm_hoist_kind(Func f, int32_t v) noexcept nogil:
    cdef uint16_t op = f.instrs[v].op
    cdef int32_t pid
    if op == OPX_GET:
        pid = f.instrs[v].aux
        return f.places[pid].kind == PLACE_REAL_BLOCK and not (f.places[pid].flags & PLACE_WRITABLE)
    if op < OP_RUNTIME_COUNT and (f.instrs[v].flags & FLAG_PURE):
        return True
    return False


cdef list _licm_operands(Func f, int32_t v):
    # Value ids this value directly consumes (for the hoist-set closure).
    cdef int32_t pid, astart, n, k
    res = []
    if f.instrs[v].op == OPX_GET:
        pid = f.instrs[v].aux
        if f.places[pid].kind == PLACE_DYNAMIC_BLOCK:
            res.append(f.places[pid].block_ref)
        if f.places[pid].index_val >= 0:
            res.append(f.places[pid].index_val)
        return res
    astart = f.instrs[v].arg_start
    n = f.instrs[v].nargs
    for k in range(n):
        res.append(<int32_t>f.args[astart + k])
    return res


cdef bint _licm_operand_inv(Func f, LoopForest F, int32_t L, int32_t a, dict inv) except -1:
    # An operand is loop-invariant if defined outside the loop, or invariant itself.
    if not F.in_loop(L, f.instrs[a].block):
        return True
    return <bint>inv.get(a, False)


cdef bint _licm_is_invariant(Func f, LoopForest F, int32_t L, int32_t v, dict inv) except -1:
    cdef uint16_t op = f.instrs[v].op
    cdef int32_t pid, astart, n, k, iv
    if op == OPX_PHI:
        return False
    if op == OPX_UNDEF or op == OPX_CONST:
        return True
    if op == OPX_GET:
        pid = f.instrs[v].aux
        if f.places[pid].kind != PLACE_REAL_BLOCK:
            return False
        if f.places[pid].flags & PLACE_WRITABLE:
            return False
        iv = f.places[pid].index_val
        if iv >= 0 and not _licm_operand_inv(f, F, L, iv, inv):
            return False
        return True
    if op < OP_RUNTIME_COUNT and (f.instrs[v].flags & FLAG_PURE):
        astart = f.instrs[v].arg_start
        n = f.instrs[v].nargs
        for k in range(n):
            if not _licm_operand_inv(f, F, L, <int32_t>f.args[astart + k], inv):
                return False
        return True
    return False


def _phi_opmap_src(Func f, int32_t vid):
    # {src_edge_index: ("o", operand_vid)} for a phi, by incoming-edge order.
    cdef int32_t b = f.instrs[vid].block
    cdef int32_t astart = f.instrs[vid].arg_start
    cdef int32_t nargs = f.instrs[vid].nargs
    cdef int32_t e, k
    inc = []
    for e in range(f.n_edges):
        if f.edges[e].dst == b:
            inc.append(e)
    opmap = {}
    for k in range(nargs):
        opmap[<int32_t>inc[k]] = ("o", <int32_t>f.args[astart + k])
    return opmap


def _licm_try_loop(Func f, Dominators D, LoopForest F, int32_t L):
    cdef int32_t header = F.header[L]
    cdef int32_t entry = f.entry_block
    cdef int32_t nb = f.n_blocks
    cdef int32_t e, u, vid, b, a
    if header == entry:
        return None

    # latches: tails of back edges into the header.
    latches = []
    for e in range(f.n_edges):
        if f.edges[e].dst == header and D.dominates(header, f.edges[e].src):
            latches.append(f.edges[e].src)
    if not latches:
        return None

    # invariant set: single forward pass over loop-body values (operands of a
    # non-phi value have strictly smaller ids, so one pass suffices; phis are
    # never invariant and break any cycle).
    inv = {}
    for vid in range(f.n_instrs):
        b = f.instrs[vid].block
        if not F.in_loop(L, b):
            continue
        inv[vid] = _licm_is_invariant(f, F, L, vid, inv)

    # hoist roots: invariant, hoistable kind, guaranteed-to-execute, cost >= 4.
    memo_cost = {}
    memo_rtc = {}
    roots = []
    for vid in range(f.n_instrs):
        b = f.instrs[vid].block
        if not F.in_loop(L, b):
            continue
        if not <bint>inv.get(vid, False):
            continue
        if not _licm_hoist_kind(f, vid):
            continue
        guaranteed = True
        for u in latches:
            if not D.dominates(b, u):
                guaranteed = False
                break
        if not guaranteed:
            continue
        if _licm_eff_cost(f, vid, memo_cost, memo_rtc) < 4:
            continue
        roots.append(vid)
    if not roots:
        return None

    # closure over in-loop (invariant) operands so the preheader is self-contained.
    H = set()
    work = list(roots)
    while work:
        v = <int32_t>work.pop()
        if v in H:
            continue
        if not F.in_loop(L, f.instrs[v].block):
            continue
        if not <bint>inv.get(v, False):
            continue
        H.add(v)
        for a in _licm_operands(f, v):
            work.append(a)
    if not H:
        return None

    return _licm_apply(f, D, F, L, header, H)


def _licm_apply(Func f, Dominators D, LoopForest F, int32_t L, int32_t header, set H):
    cdef int32_t nb = f.n_blocks
    cdef int32_t b, vid, istart, icount, es, ec, e, pred

    # base model: one plan-block per src block (index == src block id).
    pblocks = []
    for b in range(nb):
        items = []
        istart = f.blocks[b].instr_start
        icount = f.blocks[b].instr_count
        for vid in range(istart, istart + icount):
            if vid in H:
                continue  # hoisted values are relocated to the preheader
            if f.instrs[vid].op == OPX_PHI:
                items.append(("i", vid, _phi_opmap_src(f, vid)))
            else:
                items.append(("i", vid, None))
        test = ("o", f.blocks[b].test_val) if f.blocks[b].test_val >= 0 else None
        edges = []
        es = f.blocks[b].edge_start
        ec = f.blocks[b].edge_count
        for e in range(es, es + ec):
            edges.append([f.edges[e].dst, f.edges[e].cond_kind, f.edges[e].cond, f.edges[e].cond_is_int, e])
        pblocks.append({"items": items, "test": test, "edges": edges})

    entry_pb = f.entry_block

    # header incoming edges: entry (from outside the loop) vs back (from inside).
    entry_edges = []
    back_edges = []
    for e in range(f.n_edges):
        if f.edges[e].dst != header:
            continue
        if F.in_loop(L, f.edges[e].src):
            back_edges.append(e)
        else:
            entry_edges.append(e)
    if not entry_edges:
        return None

    # hoisted values in dependency order (ascending id == def-before-use).
    hoist_items = [("i", v, None) for v in sorted(H)]

    # reuse an existing clean preheader (single entry edge whose src has exactly
    # one outgoing edge and no phis), else create one.
    reuse_pre = -1
    if len(entry_edges) == 1:
        pred = f.edges[entry_edges[0]].src
        if f.blocks[pred].edge_count == 1 and f.blocks[pred].phi_count == 0:
            reuse_pre = pred

    if reuse_pre >= 0:
        (<dict>pblocks[reuse_pre])["items"].extend(hoist_items)
        return _emit_from_model(f, pblocks, entry_pb)

    # create a new preheader.
    pre_pb = len(pblocks)
    p_key = ("licm_pre", header)
    pre_phis = []
    tok_ctr = 0
    header_pb = <dict>pblocks[header]
    new_header_items = []
    for it in <list>header_pb["items"]:
        if <str>it[0] == "i" and f.instrs[<int32_t>it[1]].op == OPX_PHI:
            old_opmap = <dict>it[2]
            if len(entry_edges) == 1:
                pre_operand = old_opmap[entry_edges[0]]
            else:
                tok = ("licm_np", header, tok_ctr)
                tok_ctr += 1
                np_opmap = {}
                for e in entry_edges:
                    np_opmap[e] = old_opmap[e]
                pre_phis.append(("np", tok, f.instrs[<int32_t>it[1]].aux, np_opmap))
                pre_operand = ("n", tok)
            new_opmap = {p_key: pre_operand}
            for e in back_edges:
                new_opmap[e] = old_opmap[e]
            new_header_items.append(("i", <int32_t>it[1], new_opmap))
        else:
            new_header_items.append(it)
    header_pb["items"] = new_header_items

    # retarget entry edges to the preheader (keep their edge keys).
    for e in entry_edges:
        src_b = f.edges[e].src
        for espec in <list>(<dict>pblocks[src_b])["edges"]:
            if espec[4] == e:
                espec[0] = pre_pb
                break

    pre_items = list(pre_phis) + hoist_items
    pre_edges = [[header, EDGE_COND_NONE, 0.0, 0, p_key]]
    pblocks.append({"items": pre_items, "test": None, "edges": pre_edges})
    return _emit_from_model(f, pblocks, entry_pb)


def _licm_pass_once(Func f):
    cdef Dominators D = compute_dominators(f)
    cdef LoopForest F = compute_loops(f, D)
    cdef int32_t nl = F.n_loops
    cdef int32_t L
    if nl == 0:
        return None
    # inner-first: process loops by header block id descending (mirrors the old
    # LICM, which sorted headers by num descending).
    order = sorted(range(nl), key=lambda li: F.header[li], reverse=True)
    for L in order:
        nf = _licm_try_loop(f, D, F, <int32_t>L)
        if nf is not None:
            return nf
    return None


def _run_licm(Func f):
    cur = f
    any_changed = False
    cdef int32_t cap = f.n_instrs + f.n_blocks + 16
    cdef int32_t iters = 0
    while iters < cap:
        iters += 1
        nf = _licm_pass_once(<Func>cur)
        if nf is None:
            break
        cur = nf
        any_changed = True
    return (cur, any_changed)


# ---- rewrite_switch (7.2.6 / section 4) ----------------------------------
# Mirrors the old RewriteToSwitch: (1) a two-way block {VALUE 0 -> false, NONE ->
# true} whose test is Equal(x, C) with C an OPX_CONST becomes test=x with the true
# edge carrying cond=C and the false edge becoming the NONE default; (2) chain
# splicing: while the default target is an empty single-pred block with the same
# test, splice its cases up (dropping duplicate conds) and let it die. Runs on SSA
# via _build_compacted (tests mutated in place; no instrs move).


cdef bint _rsw_block_empty(Func f, int32_t b) noexcept nogil:
    cdef int32_t i, istart, icount
    if f.blocks[b].phi_count > 0:
        return False
    istart = f.blocks[b].instr_start
    icount = f.blocks[b].instr_count
    for i in range(istart, istart + icount):
        if f.instrs[i].flags & FLAG_STMT_ROOT:
            return False
    return True


def _rsw_ext_ref(Func f):
    # Set of value ids referenced from a block OTHER than their defining block
    # (operands, phi operands, block tests, and dynamic place index/block refs).
    # A chain block that is empty of statements/phis may still *define* a value
    # (a short-circuit Equal, or -- because SSA places consts in a dominator block
    # -- a downstream phi-operand const) used by a survivor; splicing it away would
    # orphan that value. Consts are freely relocatable (moved to entry on splice);
    # a non-const external reference makes the block un-spliceable.
    cdef int32_t i, bi, op, astart, k, a, pid, iv, br, b, tv
    ext = set()
    for i in range(f.n_instrs):
        bi = f.instrs[i].block
        op = f.instrs[i].op
        astart = f.instrs[i].arg_start
        for k in range(f.instrs[i].nargs):
            a = <int32_t>f.args[astart + k]
            if f.instrs[a].block != bi:
                ext.add(a)
        if op == OPX_GET or op == OPX_SET:
            pid = f.instrs[i].aux
            iv = f.places[pid].index_val
            if iv >= 0 and f.instrs[iv].block != bi:
                ext.add(iv)
            if f.places[pid].kind == PLACE_DYNAMIC_BLOCK:
                br = f.places[pid].block_ref
                if f.instrs[br].block != bi:
                    ext.add(br)
    for b in range(f.n_blocks):
        tv = f.blocks[b].test_val
        if tv >= 0 and f.instrs[tv].block != b:
            ext.add(tv)
    return ext


cdef bint _rsw_relocatable(Func f, int32_t nxt, set ext) except -1:
    # nxt is spliceable iff every value it defines that is referenced externally is
    # an OPX_CONST (relocatable to entry).
    cdef int32_t vid, istart = f.blocks[nxt].instr_start, icount = f.blocks[nxt].instr_count
    for vid in range(istart, istart + icount):
        if vid in ext and f.instrs[vid].op != OPX_CONST:
            return False
    return True


def _rsw_incoming_count(list out, int32_t nb, int32_t target):
    cdef int32_t b, c = 0
    for b in range(nb):
        for ed in <list>out[b]:
            if <int32_t>(<dict>ed)["dst"] == target:
                c += 1
    return c


def _rsw_rpo(list out, int32_t entry, int32_t nb):
    visited = [False] * nb
    post = []
    stack = [(entry, 0)]
    visited[entry] = True
    cdef int32_t node, idx
    while stack:
        top = stack[len(stack) - 1]
        node = <int32_t>top[0]
        idx = <int32_t>top[1]
        edges = <list>out[node]
        if idx < len(edges):
            stack[len(stack) - 1] = (node, idx + 1)
            ch = <int32_t>(<dict>edges[idx])["dst"]
            if not <bint>visited[ch]:
                visited[ch] = True
                stack.append((ch, 0))
        else:
            post.append(node)
            stack.pop()
    return list(reversed(post))


def _run_rewrite_switch(Func f):
    cdef int32_t nb = f.n_blocks
    cdef int32_t b, e, es, ec, tv, a0, a1, const_v, other_v
    cdef int32_t entry = f.entry_block
    cdef bint changed = False

    # mutable edge model.
    out = [[] for _ in range(nb)]
    for b in range(nb):
        es = f.blocks[b].edge_start
        ec = f.blocks[b].edge_count
        for e in range(es, es + ec):
            (<list>out[b]).append({
                "dst": f.edges[e].dst, "ck": f.edges[e].cond_kind,
                "cond": f.edges[e].cond, "ci": f.edges[e].cond_is_int, "key": e,
            })
    test_val = [f.blocks[b].test_val for b in range(nb)]

    # (1) ifs_to_switch.
    for b in range(nb):
        edges = <list>out[b]
        if len(edges) != 2:
            continue
        val_edge = None
        none_edge = None
        for ed in edges:
            if <int32_t>(<dict>ed)["ck"] == EDGE_COND_VALUE and (<dict>ed)["cond"] == 0.0:
                val_edge = <dict>ed
            elif <int32_t>(<dict>ed)["ck"] == EDGE_COND_NONE:
                none_edge = <dict>ed
        if val_edge is None or none_edge is None:
            continue
        tv = <int32_t>test_val[b]
        if tv < 0 or f.instrs[tv].op != OP_Equal or f.instrs[tv].nargs != 2:
            continue
        a0 = <int32_t>f.args[f.instrs[tv].arg_start]
        a1 = <int32_t>f.args[f.instrs[tv].arg_start + 1]
        if f.instrs[a0].op == OPX_CONST:
            const_v = a0
            other_v = a1
        elif f.instrs[a1].op == OPX_CONST:
            other_v = a0
            const_v = a1
        else:
            continue
        test_val[b] = other_v
        none_edge["ck"] = EDGE_COND_VALUE
        none_edge["cond"] = f.consts[f.instrs[const_v].aux]
        none_edge["ci"] = 1 if (f.instrs[const_v].flags & FLAG_CONST_IS_INT) else 0
        val_edge["ck"] = EDGE_COND_NONE
        val_edge["cond"] = 0.0
        val_edge["ci"] = 0
        changed = True

    # (2) combine_blocks: splice same-test empty single-pred default chains.
    ext = _rsw_ext_ref(f)
    processed = set()
    queue = [entry]
    while queue:
        b = <int32_t>queue.pop()
        if b in processed:
            continue
        processed.add(b)
        for ed in <list>out[b]:
            queue.append(<int32_t>(<dict>ed)["dst"])
        default = None
        for ed in <list>out[b]:
            if <int32_t>(<dict>ed)["ck"] == EDGE_COND_NONE:
                default = <dict>ed
                break
        if default is None:
            continue
        nxt = <int32_t>default["dst"]
        if b == nxt or nxt == entry:
            continue
        if <int32_t>test_val[b] < 0 or test_val[b] != test_val[nxt]:
            continue
        if not _rsw_block_empty(f, nxt):
            continue
        if _rsw_incoming_count(out, nb, nxt) > 1:
            continue
        # splice-safety: nxt must define no non-const value used by a survivor
        # (see _rsw_ext_ref). Escaping consts are relocated to entry below.
        if not _rsw_relocatable(f, nxt, ext):
            continue
        # splice.
        existing = set()
        for ed in <list>out[b]:
            if <int32_t>(<dict>ed)["ck"] == EDGE_COND_VALUE:
                existing.add((<dict>ed)["cond"])
        kept = [ed for ed in <list>out[b] if ed is not default]
        for ed in <list>out[nxt]:
            if <int32_t>(<dict>ed)["ck"] == EDGE_COND_VALUE and (<dict>ed)["cond"] in existing:
                continue  # duplicate cond: unreachable, drop
            kept.append(ed)
            if <int32_t>(<dict>ed)["ck"] == EDGE_COND_VALUE:
                existing.add((<dict>ed)["cond"])
        out[b] = kept
        out[nxt] = []  # nxt is now unreachable
        changed = True
        processed.discard(b)
        queue.append(b)

    if not changed:
        return (f, False)

    # apply: build a value model reflecting the mutated tests + edges, relocating
    # any externally-referenced const from a now-unreachable block into entry (its
    # own block dies but its value is still a live phi operand / operand elsewhere),
    # then rebuild via the general model builder.
    reach = set(_rsw_rpo(out, entry, nb))
    pblocks = []
    cdef int32_t vid, istart, icount
    for b in range(nb):
        items = []
        istart = f.blocks[b].instr_start
        icount = f.blocks[b].instr_count
        for vid in range(istart, istart + icount):
            if f.instrs[vid].op == OPX_PHI:
                items.append(("i", vid, _phi_opmap_src(f, vid)))
            else:
                items.append(("i", vid, None))
        tv2 = <int32_t>test_val[b]
        test = ("o", tv2) if tv2 >= 0 else None
        edges = []
        for ed in <list>out[b]:
            edges.append([
                <int32_t>(<dict>ed)["dst"], <int32_t>(<dict>ed)["ck"],
                <double>(<dict>ed)["cond"], <int32_t>(<dict>ed)["ci"], <int32_t>(<dict>ed)["key"],
            ])
        pblocks.append({"items": items, "test": test, "edges": edges})
    relocate = []
    for b in range(nb):
        if b in reach:
            continue
        istart = f.blocks[b].instr_start
        icount = f.blocks[b].instr_count
        for vid in range(istart, istart + icount):
            if f.instrs[vid].op == OPX_CONST and vid in ext:
                relocate.append(vid)
    if relocate:
        (<dict>pblocks[entry])["items"].extend([("i", v, None) for v in relocate])
    newf = _emit_from_model(f, pblocks, entry)
    return (newf, True)


def _run_sccp(Func f):
    cdef _SCCP sc = _SCCP(f)
    sc.run()
    order, keep_instr, const_override, out_spec, changed = sc.decisions()
    if not changed:
        return (f, False)
    newf = _build_compacted(f, order, keep_instr, const_override, out_spec)
    # Edge pruning can leave single-operand phis; collapse them so SCCP's SSA
    # output has no degenerate phis (the dead phi instrs are dropped by DCE).
    _collapse_trivial_phis(<Func>newf)
    return (newf, True)


# --------------------------------------------------------------------------
# Pass entry points + the change-driven round (7.2.7).
# --------------------------------------------------------------------------

cdef Func sccp(Func func):
    res = _run_sccp(func)
    return <Func>res[0]


cdef Func gvn(Func func):
    res = _run_gvn_inplace(func)
    return <Func>res[0]


cdef Func dce(Func func):
    res = _run_dce(func)
    return <Func>res[0]


cdef Func licm(Func func):
    res = _run_licm(func)
    return <Func>res[0]


cdef Func rewrite_switch(Func func):
    res = _run_rewrite_switch(func)
    return <Func>res[0]


# --------------------------------------------------------------------------
# Op-level Pointed/Shifted read-modify-write fusion (M3.5, OPTIMIZER_REWRITE.md).
# Runs on SSA AFTER GVN (so identical address args share value ids) and BEFORE
# DCE (which reaps the orphaned GetPointed/BinOp). The frontend never emits
# Pointed/Shifted ops, so this never fires on the pydori corpus -- it is guarded
# by a cheap pre-scan so the common (no-such-op) case allocates nothing.
# --------------------------------------------------------------------------

cdef inline int32_t _fused_ptr_op(uint16_t binop, bint shifted) noexcept nogil:
    if not shifted:
        if binop == <uint16_t>OP_Add:
            return OP_SetAddPointed
        if binop == <uint16_t>OP_Subtract:
            return OP_SetSubtractPointed
        if binop == <uint16_t>OP_Multiply:
            return OP_SetMultiplyPointed
        if binop == <uint16_t>OP_Divide:
            return OP_SetDividePointed
        if binop == <uint16_t>OP_Mod:
            return OP_SetModPointed
        if binop == <uint16_t>OP_Rem:
            return OP_SetRemPointed
        if binop == <uint16_t>OP_Power:
            return OP_SetPowerPointed
        return -1
    if binop == <uint16_t>OP_Add:
        return OP_SetAddShifted
    if binop == <uint16_t>OP_Subtract:
        return OP_SetSubtractShifted
    if binop == <uint16_t>OP_Multiply:
        return OP_SetMultiplyShifted
    if binop == <uint16_t>OP_Divide:
        return OP_SetDivideShifted
    if binop == <uint16_t>OP_Mod:
        return OP_SetModShifted
    if binop == <uint16_t>OP_Rem:
        return OP_SetRemShifted
    if binop == <uint16_t>OP_Power:
        return OP_SetPowerShifted
    return -1


cdef inline bint _is_one(Func f, int32_t vid) noexcept nogil:
    return f.instrs[vid].op == <uint16_t>OPX_CONST and f.consts[f.instrs[vid].aux] == 1.0


cdef bint _no_effect_between(Func f, int32_t lo, int32_t hi) noexcept nogil:
    # No FLAG_SIDE_EFFECT instr strictly between linear indices lo and hi (same
    # block; pinned/effectful instrs keep program order, so the linear scan is the
    # program-order scan -- mirrors treeify's pinned-read fold guard).
    cdef int32_t j
    for j in range(lo + 1, hi):
        if f.instrs[j].flags & FLAG_SIDE_EFFECT:
            return False
    return True


def _fuse_ptr_rmw(Func f):
    cdef int32_t n = f.n_instrs
    cdef int32_t i, op
    # Cheap pre-scan: bail (allocating nothing) unless a Set{Pointed,Shifted}
    # statement root exists. The corpus has none, so this is the taken path.
    cdef bint present = False
    for i in range(n):
        op = f.instrs[i].op
        if (op == <uint16_t>OP_SetPointed or op == <uint16_t>OP_SetShifted) and (
            f.instrs[i].flags & FLAG_STMT_ROOT
        ):
            present = True
            break
    if not present:
        return False

    cdef int32_t b, tv, astart, nargs, k, a, pid, ivv
    uc = [0] * n
    for i in range(n):
        op = f.instrs[i].op
        astart = f.instrs[i].arg_start
        nargs = f.instrs[i].nargs
        for k in range(nargs):
            a = <int32_t>f.args[astart + k]
            uc[a] = <int32_t>uc[a] + 1
        if op == OPX_GET or op == OPX_SET:
            pid = f.instrs[i].aux
            if f.places[pid].kind == PLACE_DYNAMIC_BLOCK:
                a = f.places[pid].block_ref
                uc[a] = <int32_t>uc[a] + 1
            ivv = f.places[pid].index_val
            if ivv >= 0:
                uc[ivv] = <int32_t>uc[ivv] + 1
    for b in range(f.n_blocks):
        tv = f.blocks[b].test_val
        if tv >= 0:
            uc[tv] = <int32_t>uc[tv] + 1

    cdef bint changed = False
    for i in range(n):
        op = f.instrs[i].op
        if not (f.instrs[i].flags & FLAG_STMT_ROOT):
            continue
        if op == <uint16_t>OP_SetPointed:
            if _try_fuse_ptr(f, i, uc, 3, <uint16_t>OP_GetPointed, False):
                changed = True
        elif op == <uint16_t>OP_SetShifted:
            if _try_fuse_ptr(f, i, uc, 4, <uint16_t>OP_GetShifted, True):
                changed = True
    return changed


cdef bint _try_fuse_ptr(Func f, int32_t i, list uc, int32_t naddr, uint16_t get_op, bint shifted) except -1:
    cdef int32_t astart = f.instrs[i].arg_start
    if f.instrs[i].nargs != naddr + 1:
        return False
    cdef int32_t vid = <int32_t>f.args[astart + naddr]  # value operand (the binop)
    if <int32_t>uc[vid] != 1 or f.instrs[vid].nargs != 2:
        return False
    cdef uint16_t vop = f.instrs[vid].op
    cdef int32_t fused = _fused_ptr_op(vop, shifted)
    if fused < 0:
        return False
    cdef int32_t g = <int32_t>f.args[f.instrs[vid].arg_start]
    cdef int32_t w = <int32_t>f.args[f.instrs[vid].arg_start + 1]
    # args[0] of the binop must be a single-use Get{Pointed,Shifted} whose address
    # value-ids (GVN'd) equal the store's address value-ids. Single-use on ``g``
    # also guarantees ``w`` cannot reference it (evaluation-order guard for free).
    if f.instrs[g].op != get_op or f.instrs[g].nargs != naddr or <int32_t>uc[g] != 1:
        return False
    cdef int32_t gstart = f.instrs[g].arg_start
    cdef int32_t k
    for k in range(naddr):
        if <int32_t>f.args[astart + k] != <int32_t>f.args[gstart + k]:
            return False
    # The read must be immediately consumed: same block, no effect between it and
    # the store (pinned-read guard, mirroring treeify).
    if f.instrs[g].block != f.instrs[i].block:
        return False
    if not _no_effect_between(f, g, i):
        return False
    cdef int32_t inc_op, dec_op
    if not shifted:
        inc_op = OP_IncrementPostPointed
        dec_op = OP_DecrementPostPointed
    else:
        inc_op = OP_IncrementPostShifted
        dec_op = OP_DecrementPostShifted
    if vop == <uint16_t>OP_Add and _is_one(f, w):
        f.instrs[i].op = <uint16_t>inc_op
        f.instrs[i].nargs = <int16_t>naddr
    elif vop == <uint16_t>OP_Subtract and _is_one(f, w):
        f.instrs[i].op = <uint16_t>dec_op
        f.instrs[i].nargs = <int16_t>naddr
    else:
        f.instrs[i].op = <uint16_t>fused
        f.args[astart + naddr] = <uint32_t>w  # replace the binop operand with w
        # nargs stays naddr + 1
    return True


def _midend_pass(Func func):
    f1, c1 = _run_sccp(func)  # includes single-operand phi collapse
    _, c2 = _run_gvn_inplace(<Func>f1)
    cb = _canon_branch_not(<Func>f1)  # If(Not) two-way branch canon (finding #2)
    cf = _fuse_ptr_rmw(<Func>f1)  # op-level Pointed/Shifted RMW fusion (M3.5)
    f2, c3 = _run_dce(<Func>f1)  # reaps the orphaned GetPointed/BinOp + freed Not
    return (f2, c1 or c2 or c3 or cf or cb)


cdef Func midend_round(Func func, bint allow_repeat):
    res = _midend_pass(func)
    cdef Func cur = <Func>res[0]
    cdef bint ch = <bint>res[1]
    if allow_repeat and ch:
        res = _midend_pass(cur)
        cur = <Func>res[0]
    return cur


cdef Func midend_standard(Func func):
    # Standard (-O2) mid-end round (7.2.7): core (SCCP -> GVN -> DCE) -> LICM ->
    # rewrite_switch, then repeat the core ONCE if the core, LICM, or
    # rewrite_switch changed anything. LICM and rewrite_switch themselves run once.
    res = _midend_pass(func)
    cdef Func cur = <Func>res[0]
    cdef bint ch = <bint>res[1]
    lres = _run_licm(cur)
    cur = <Func>lres[0]
    cdef bint lch = <bint>lres[1]
    rres = _run_rewrite_switch(cur)
    cur = <Func>rres[0]
    cdef bint rch = <bint>rres[1]
    if ch or lch or rch:
        res = _midend_pass(cur)
        cur = <Func>res[0]
    return cur


# --------------------------------------------------------------------------
# Debug-phase registration + Python-callable test/debug entry points.
# --------------------------------------------------------------------------

def _phase_sccp(func):
    return sccp(<Func>func)


def _phase_gvn(func):
    return gvn(<Func>func)


def _phase_dce(func):
    return dce(<Func>func)


def _phase_midend(func):
    return midend_round(<Func>func, True)


def _phase_licm(func):
    return licm(<Func>func)


def _phase_rewrite_switch(func):
    return rewrite_switch(<Func>func)


def _phase_midend_standard(func):
    return midend_standard(<Func>func)


register_phase("sccp", _phase_sccp)
register_phase("gvn", _phase_gvn)
register_phase("dce", _phase_dce)
register_phase("midend", _phase_midend)
register_phase("licm", _phase_licm)
register_phase("rewrite_switch", _phase_rewrite_switch)
register_phase("midend_standard", _phase_midend_standard)


def run_midend(entry, mode=None, callback=None, allow_repeat=False):
    """marshal_in -> cfg_cleanup -> build_ssa -> midend_round -> out_of_ssa -> export.

    The full mid-end, testable end-to-end today (the treeify agent is building the
    real lowering in parallel; this uses the naive ``out_of_ssa``).
    """
    cdef Func func = cfg_cleanup(<Func>marshal_in(entry, mode, callback), False)
    cdef Func ssa = build_ssa(func)
    cdef Func opt = midend_round(ssa, <bint>allow_repeat)
    cdef Func lowered = out_of_ssa(opt)
    return to_basic_blocks(lowered)


def run_midend_standard(entry, mode=None, callback=None):
    """marshal_in -> cfg_cleanup -> build_ssa -> midend_standard -> out_of_ssa -> export.

    The full standard (-O2) mid-end (7.2.7: core round + LICM + rewrite_switch),
    testable end-to-end (uses the naive ``out_of_ssa`` until the treeify lowering
    is wired). This is the ``standard`` mid-end entry the driver will call.
    """
    cdef Func func = cfg_cleanup(<Func>marshal_in(entry, mode, callback), False)
    cdef Func ssa = build_ssa(func)
    cdef Func opt = midend_standard(ssa)
    cdef Func lowered = out_of_ssa(opt)
    return to_basic_blocks(lowered)


# Importing driver here guarantees the debug-phase registry is complete no matter
# which of midend/driver is imported first (they mutually register phases; driver
# cimports midend so this only creates a benign runtime import edge). Without it,
# importing midend directly would pre-empt debug_run's lazy driver import and hide
# the cfg_cleanup/ssa/unssa phases.
from sonolus.backend._opt import driver as _driver  # noqa: F401, E402
