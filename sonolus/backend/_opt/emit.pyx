# cython: language_level=3
"""EngineNode emission from the arena IR.

Builds the EngineNode tree directly from the flat ``Func`` arena (see ir.pxd),
with one deliberate rewrite on the way out: associative left spines
(``Add``/``Multiply``/``Mod``/``Rem``, ``args[0]`` only) are re-flattened as the
tree is built. Because marshal-in *binarises* n-ary associative input (ir.pyx
``_emit_pure``), re-flattening keeps the two emit paths in agreement: the fused
``optimize_and_finalize`` path emits from n-ary trees, while the test/golden path
(export -> ``cfg_to_engine_node``) round-trips through marshal-in; without it the
latter would emit ``Add(Add(a,b),c)`` where the former emits ``Add(a,b,c)``.

Emission also performs intra-callback hash-consing on the arena so structurally
equal subtrees become the *same* ``FunctionNode`` object.

--------------------------------------------------------------------------------
HASH-CONSING POLICY
--------------------------------------------------------------------------------
Every emitted node -- leaf (``int``/``float``) and ``FunctionNode`` alike -- is
interned by a structural key built from integer identities:

* a ``FunctionNode`` key is ``(id(op_member), id(arg0), id(arg1), ...)`` where the
  arg ids are the identities of already-interned children (O(1) per node -- no
  recursive Python tree hashing);
* leaf ints and leaf floats are interned in separate value-keyed tables so that
  the display forms ``5`` and ``5.0`` never collapse (they are distinct in
  ``SwitchWithDefault`` case labels), while a canonical integral value like a
  demoted const is always the same ``int`` object.

Interned objects are pinned in a list so their ``id()`` stays stable and unique
for the emitter's lifetime.

Consing is applied to **all** ops, pure and impure (``Random``, ``Get`` reads,
``Set``/``Draw``/... stores), not just pure ones. This is correct because:

* the downstream ``OutputNodeGenerator`` already dedups the emitted tree by
  structural (value) equality, so emit-level structural consing is a strict
  subset of that and cannot change the final artifact;
* the real Sonolus runtime (and the ``interpret.py`` oracle) *re-executes* every
  reference to a shared node -- consing shrinks the artifact, it never memoises
  execution -- so two structurally identical ``Random()`` reads still draw
  independently at their two reference sites, and two identical ``Get`` reads
  separated by a store still observe the store. Consing therefore preserves
  semantics for impure ops while keeping the implementation uniform.

The one thing consing must never do is merge nodes that are *not* structurally
identical, which the exact keys guarantee.
"""

from libc.stdint cimport int32_t, uint16_t
from libc.math cimport isfinite, isinf, isnan

from sonolus.backend._opt.ir cimport (
    Func,
    FLAG_STMT_ROOT,
    PLACE_REAL_BLOCK,
    PLACE_DYNAMIC_BLOCK,
    EDGE_COND_NONE,
)
from sonolus.backend._opt._ops_gen cimport (
    OPX_CONST,
    OPX_GET,
    OPX_SET,
    OP_Add,
    OP_DecrementPost,
    OP_DecrementPostShifted,
    OP_IncrementPost,
    OP_IncrementPostShifted,
    OP_Multiply,
    OP_Mod,
    OP_Rem,
    OP_RUNTIME_COUNT,
    OP_SetAdd,
    OP_SetAddShifted,
    OP_SetDivide,
    OP_SetDivideShifted,
    OP_SetMod,
    OP_SetModShifted,
    OP_SetMultiply,
    OP_SetMultiplyShifted,
    OP_SetPower,
    OP_SetPowerShifted,
    OP_SetRem,
    OP_SetRemShifted,
    OP_SetSubtract,
    OP_SetSubtractShifted,
)

from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op as _Op

# Runtime op id -> Op member (ops.py declaration order == id order).
_ID_TO_OP = list(_Op)

# Op members referenced directly by the terminator / place / wrapper builders.
_OP_IF = _Op.If
_OP_EQUAL = _Op.Equal
_OP_GET = _Op.Get
_OP_SET = _Op.Set
_OP_ADD = _Op.Add
_OP_EXECUTE = _Op.Execute
_OP_BLOCK = _Op.Block
_OP_JUMPLOOP = _Op.JumpLoop
_OP_SWITCH_INT_DEFAULT = _Op.SwitchIntegerWithDefault
_OP_SWITCH_DEFAULT = _Op.SwitchWithDefault
_OP_GET_SHIFTED = _Op.GetShifted
_OP_SET_SHIFTED = _Op.SetShifted

# EngineRom block id: NaN/+-Inf constants are lowered to reads from it.
cdef int32_t _ENGINE_ROM = 3000


cdef inline int32_t _shifted_fused_op(uint16_t op) noexcept nogil:
    """Map a place-based fused RMW op (fuse_rmw output) to its strided ``*Shifted``
    form, or -1 if the op is not a fused RMW."""
    if op == <uint16_t>OP_SetAdd:
        return OP_SetAddShifted
    if op == <uint16_t>OP_SetSubtract:
        return OP_SetSubtractShifted
    if op == <uint16_t>OP_SetMultiply:
        return OP_SetMultiplyShifted
    if op == <uint16_t>OP_SetDivide:
        return OP_SetDivideShifted
    if op == <uint16_t>OP_SetMod:
        return OP_SetModShifted
    if op == <uint16_t>OP_SetRem:
        return OP_SetRemShifted
    if op == <uint16_t>OP_SetPower:
        return OP_SetPowerShifted
    if op == <uint16_t>OP_IncrementPost:
        return OP_IncrementPostShifted
    if op == <uint16_t>OP_DecrementPost:
        return OP_DecrementPostShifted
    return -1


cdef inline bint _is_flattenable(uint16_t op) noexcept nogil:
    return op == <uint16_t>OP_Add or op == <uint16_t>OP_Multiply or op == <uint16_t>OP_Mod or op == <uint16_t>OP_Rem


cdef bint _cond_is_integral(object cond):
    # Integrality gate for the dense-switch shortcut. A Python int is always
    # integral; a float must be finite before int() (which raises OverflowError on
    # +-inf and ValueError on NaN) -- non-finite conds are never dense cases.
    if type(cond) is int:
        return True
    if not isfinite(<double>cond):
        return False
    return int(cond) == cond


cdef class _Emitter:
    """Per-callback emission state: the arena plus the hash-cons tables."""

    cdef Func func
    cdef dict _fn_table     # (id(op), id(child)...) -> FunctionNode
    cdef dict _int_leaves   # python int value -> that int object (interned)
    cdef dict _float_leaves # python float value -> that float object (interned)
    cdef dict _val_cache    # arena value id -> EngineNode (memo, tolerates shared vids)
    cdef list _pin          # keeps interned objects alive so their id() is stable
    cdef list _block_map    # old block id -> emitted index (elided -> exit index)
    cdef int32_t _exit_index  # index of the trailing halt sentinel (# emitted blocks)

    def __cinit__(self, Func func):
        self.func = func
        self._fn_table = {}
        self._int_leaves = {}
        self._float_leaves = {}
        self._val_cache = {}
        self._pin = []
        self._block_map = None
        self._exit_index = func.n_blocks

    # -- leaf / function-node interning ------------------------------------

    cdef object _int_leaf(self, object v):
        obj = self._int_leaves.get(v)
        if obj is None:
            self._int_leaves[v] = v
            self._pin.append(v)
            return v
        return obj

    cdef object _float_leaf(self, object v):
        obj = self._float_leaves.get(v)
        if obj is None:
            self._float_leaves[v] = v
            self._pin.append(v)
            return v
        return obj

    cdef object _intern_fn(self, object op_member, list children):
        cdef list keyparts = [id(op_member)]
        cdef object c
        for c in children:
            keyparts.append(id(c))
        cdef tuple key = tuple(keyparts)
        node = self._fn_table.get(key)
        if node is None:
            node = FunctionNode(op_member, tuple(children))
            self._fn_table[key] = node
            self._pin.append(node)
        return node

    # -- numeric constant emission ----------------------------------------

    cdef object _rom_read(self, int32_t k):
        return self._intern_fn(_OP_GET, [self._int_leaf(_ENGINE_ROM), self._int_leaf(k)])

    cdef object _emit_numeric(self, double v):
        # Int-demote integral floats (incl. -0.0 -> int 0); finite non-integral
        # floats emit as-is; +-Inf and NaN lower to EngineRom reads. (-0.0 never
        # reaches here as a materialized const: SCCP declines to materialize a
        # folded -0.0, and the frontend IRConst collapses -0.0 -> +0.0, so the
        # arena has no standalone -0.0 const to emit. See midend.pyx decisions().)
        cdef object pv
        if isnan(v):
            return self._rom_read(0)
        if isinf(v):
            return self._rom_read(1 if v > 0 else 2)
        pv = v  # a python float
        if pv.is_integer():
            return self._int_leaf(int(pv))
        return self._float_leaf(pv)

    cdef object _leaf_cond(self, object cond):
        # Switch-case labels keep their int/float display form verbatim.
        if type(cond) is int:
            return self._int_leaf(cond)
        return self._float_leaf(cond)

    # -- value trees -------------------------------------------------------

    cdef object _emit_value(self, int32_t vid):
        cached = self._val_cache.get(vid)
        if cached is not None:
            return cached
        cdef uint16_t op = self.func.instrs[vid].op
        cdef object result
        if op == <uint16_t>OPX_CONST:
            result = self._emit_numeric(self.func.consts[self.func.instrs[vid].aux])
        elif op == <uint16_t>OPX_GET:
            result = self._emit_get(self.func.instrs[vid].aux)
        elif op == <uint16_t>OPX_SET:
            raise AssertionError("OPX_SET encountered in value position")
        else:
            result = self._emit_op(vid, op)
        self._val_cache[vid] = result
        return result

    cdef object _flatten_left_spine(self, object op_member, bint flattenable, list children):
        # Re-flatten the associative left spine.
        # ``children[0]`` was already emitted (hence already flattened), so a
        # single splice fully flattens; right-nested trees (``Add(a, Add(b,c))``)
        # are left intact, preserving left-to-right FP evaluation order.
        cdef object first
        if flattenable and len(children) > 0:
            first = children[0]
            if type(first) is FunctionNode and first.func == op_member:
                children = list(first.args) + children[1:]
        return self._intern_fn(op_member, children)

    cdef object _emit_op(self, int32_t vid, uint16_t op):
        cdef int32_t astart = self.func.instrs[vid].arg_start
        cdef int32_t nargs = self.func.instrs[vid].nargs
        cdef int32_t k
        cdef list children = []
        for k in range(nargs):
            children.append(self._emit_value(<int32_t>self.func.args[astart + k]))
        return self._flatten_left_spine(_ID_TO_OP[op], _is_flattenable(op), children)

    # -- places ------------------------------------------------------------

    cdef object _emit_get(self, int32_t pid):
        cdef tuple shifted = self._shifted_components(pid)
        if shifted is not None:
            return self._intern_fn(_OP_GET_SHIFTED, list(shifted))
        block_node, index_node = self._place_components(pid)
        return self._intern_fn(_OP_GET, [block_node, index_node])

    cdef tuple _shifted_components(self, int32_t pid):
        # Return (block, offset, index, stride) nodes when this place's address can
        # be emitted as {Get,Set}Shifted(block, offset, index, stride) ==
        # {get,set}(block, offset + index*stride), else None.
        #
        # Two shapes, both gate-safe (never grow the node count):
        #  * index is a binary ``Multiply(a, b)`` -> Shifted(block, offset, a, b):
        #    absorbs the ``Multiply`` (and the offset ``Add`` when offset != 0) into
        #    one node. The stride may be a runtime value, not just a constant --
        #    GetShifted's stride operand handles it (-2 with offset; neutral without).
        #  * a non-``Multiply`` index with a nonzero constant offset -> stride 1:
        #    absorbs the offset ``Add`` (neutral, one fewer fn dispatch; #5). Skipped
        #    when the index is itself an ``Add`` -- there the emitter folds the offset
        #    into the existing flattened ``Add`` spine for free, so shifting would ADD
        #    a node. A bare index with offset 0 stays a plain ``Get``.
        #
        # Address components are pure and evaluated block, offset, index, stride
        # left-to-right; only the compile-time-constant offset moves relative to the
        # index subtree, so no impure op is reordered (the runtime never has a Set
        # inside an address expression).
        cdef int32_t kind = self.func.places[pid].kind
        cdef int32_t block_ref = self.func.places[pid].block_ref
        cdef int32_t index_val = self.func.places[pid].index_val
        cdef int32_t offset = self.func.places[pid].offset
        cdef int32_t iastart, a0, a1
        cdef uint16_t iop
        cdef object block_node, offset_node
        if index_val < 0:
            return None  # constant index folded into offset -> plain Get(block, offset)
        if kind != <int32_t>PLACE_REAL_BLOCK and kind != <int32_t>PLACE_DYNAMIC_BLOCK:
            return None
        iop = self.func.instrs[index_val].op
        if kind == <int32_t>PLACE_REAL_BLOCK:
            block_node = self._emit_numeric(<double>block_ref)
        else:
            block_node = self._emit_value(block_ref)
        offset_node = self._emit_numeric(<double>offset)
        if iop == <uint16_t>OP_Multiply and self.func.instrs[index_val].nargs == 2:
            iastart = self.func.instrs[index_val].arg_start
            a0 = <int32_t>self.func.args[iastart]
            a1 = <int32_t>self.func.args[iastart + 1]
            return (block_node, offset_node, self._emit_value(a0), self._emit_value(a1))
        if offset != 0 and iop != <uint16_t>OP_Add:
            return (block_node, offset_node, self._emit_value(index_val), self._int_leaf(1))
        return None

    cdef tuple _place_components(self, int32_t pid):
        cdef int32_t kind = self.func.places[pid].kind
        cdef int32_t block_ref = self.func.places[pid].block_ref
        cdef int32_t index_val = self.func.places[pid].index_val
        cdef int32_t offset = self.func.places[pid].offset
        cdef object block_node, index_node
        if kind == <int32_t>PLACE_REAL_BLOCK:
            block_node = self._emit_numeric(<double>block_ref)
        elif kind == <int32_t>PLACE_DYNAMIC_BLOCK:
            block_node = self._emit_value(block_ref)
        else:
            # Temp places are rewritten to real blocks before emission (allocation);
            # a surviving temp place is a lowering bug.
            raise AssertionError(
                f"temp place kind {kind} reached emission (temps must be allocated first)"
            )
        # Index folding. Marshal-in folds a constant integer index into ``offset``
        # (index_val < 0), so:
        #   * index_val < 0            -> raw int offset  (offset==0 & const index,
        #                                 or nonzero offset & index 0: both == offset)
        #   * index_val >= 0, offset 0 -> emit(index)
        #   * index_val >= 0, offset!=0-> Add(emit(index), offset)
        if index_val < 0:
            index_node = self._emit_numeric(<double>offset)
        elif offset == 0:
            index_node = self._emit_value(index_val)
        else:
            # ``Add(index, offset)``: re-flatten its left spine too (an ``index``
            # that is itself a sum makes this a genuine associative left spine),
            # so address arithmetic is flattened uniformly with value expressions.
            index_node = self._flatten_left_spine(
                _OP_ADD, True, [self._emit_value(index_val), self._emit_numeric(<double>offset)]
            )
        return (block_node, index_node)

    # -- statements --------------------------------------------------------

    cdef object _emit_set(self, int32_t vid):
        cdef int32_t pid = self.func.instrs[vid].aux
        cdef int32_t val_vid = <int32_t>self.func.args[self.func.instrs[vid].arg_start]
        cdef tuple shifted = self._shifted_components(pid)
        cdef object value_node
        if shifted is not None:
            # SetShifted(block, offset, index, stride, value); value stays last
            # (evaluated after the address, matching Set semantics).
            value_node = self._emit_value(val_vid)
            return self._intern_fn(_OP_SET_SHIFTED, list(shifted) + [value_node])
        block_node, index_node = self._place_components(pid)
        value_node = self._emit_value(val_vid)
        return self._intern_fn(_OP_SET, [block_node, index_node, value_node])

    cdef object _emit_fused_rmw(self, int32_t i, uint16_t op):
        # Place-based fused RMW op (fuse_rmw): ``aux`` carries the place, args hold
        # the (optional) ``w`` operand. Lower to FunctionNode(Op, (block, index[, w]))
        # reusing the exact place->(block, index) folding ``Set`` emission uses. When
        # the place is strided, emit the ``*Shifted`` fused op instead
        # (Set<BinOp>Shifted(block, offset, index, stride[, w])).
        cdef int32_t pid = self.func.instrs[i].aux
        cdef int32_t astart = self.func.instrs[i].arg_start
        cdef int32_t nargs = self.func.instrs[i].nargs
        cdef int32_t k
        cdef int32_t sop = _shifted_fused_op(op)
        cdef tuple shifted = self._shifted_components(pid) if sop >= 0 else None
        cdef list children
        if shifted is not None:
            children = list(shifted)
            for k in range(nargs):
                children.append(self._emit_value(<int32_t>self.func.args[astart + k]))
            return self._intern_fn(_ID_TO_OP[sop], children)
        block_node, index_node = self._place_components(pid)
        children = [block_node, index_node]
        for k in range(nargs):
            children.append(self._emit_value(<int32_t>self.func.args[astart + k]))
        return self._intern_fn(_ID_TO_OP[op], children)

    cdef object _emit_stmt(self, int32_t i):
        cdef uint16_t op = self.func.instrs[i].op
        if op == <uint16_t>OPX_SET:
            return self._emit_set(i)
        # A runtime op carrying a place id (aux >= 0) is a place-based fused RMW op.
        if op < <uint16_t>OP_RUNTIME_COUNT and self.func.instrs[i].aux >= 0:
            return self._emit_fused_rmw(i, op)
        # A bare side-effecting op used as a statement root.
        return self._emit_value(i)

    cdef object _emit_test(self, int32_t bid):
        return self._emit_value(self.func.blocks[bid].test_val)

    # -- terminators -------------------------------------------------------

    cdef object _emit_terminator(self, int32_t bid):
        cdef int32_t estart = self.func.blocks[bid].edge_start
        cdef int32_t ecount = self.func.blocks[bid].edge_count
        cdef int32_t exit_index = self._exit_index
        cdef int32_t i
        cdef object pc, cond_key
        # Build the ``{edge.cond: edge.dst}`` dict. The arena stores this block's
        # edges already sorted (value cases ascending, then the NONE edge), so a
        # plain forward insertion yields cases ascending with the default last.
        # Targets are remapped through ``_block_map`` (elided empty shared-exit
        # blocks collapse to the exit index).
        cdef dict outgoing = {}
        for i in range(estart, estart + ecount):
            if self.func.edges[i].cond_kind == <int32_t>EDGE_COND_NONE:
                cond_key = None
            else:
                pc = self.func.edges[i].cond  # a python float
                cond_key = int(pc) if self.func.edges[i].cond_is_int else pc
            outgoing[cond_key] = <int><int32_t>self._block_map[self.func.edges[i].dst]
        return self._terminator_node(bid, outgoing, exit_index)

    cdef object _terminator_node(self, int32_t bid, dict outgoing, int32_t exit_index):
        cdef list value_conds = [c for c in outgoing if c is not None]
        cdef bint has_none = None in outgoing
        cdef object test_node, eq_node, c

        if len(outgoing) == 0:
            # {} -> constant exit index
            return self._int_leaf(exit_index)
        if len(outgoing) == 1 and has_none:
            # {None: target} -> constant index
            return self._int_leaf(outgoing[None])
        if len(outgoing) == 2 and has_none and (0 in outgoing):
            # {0: false, None: true} -> If(test, TRUE=none, FALSE=zero)
            test_node = self._emit_test(bid)
            return self._intern_fn(
                _OP_IF, [test_node, self._int_leaf(outgoing[None]), self._int_leaf(outgoing[0])]
            )
        if has_none and len(value_conds) == 1:
            # {None: default, c: branch} -> If(Equal(test, c), branch, default)
            c = value_conds[0]
            test_node = self._emit_test(bid)
            eq_node = self._intern_fn(_OP_EQUAL, [test_node, self._emit_numeric(<double>c)])
            return self._intern_fn(
                _OP_IF, [eq_node, self._int_leaf(outgoing[c]), self._int_leaf(outgoing[None])]
            )
        return self._switch_node(bid, outgoing, value_conds, has_none, exit_index)

    cdef object _switch_node(
        self, int32_t bid, dict outgoing, list value_conds, bint has_none, int32_t exit_index
    ):
        cdef list args = [self._emit_test(bid)]
        cdef int32_t default_val = exit_index
        cdef int nconds = len(value_conds)
        cdef object cond, target
        cdef int32_t ci, span
        cdef object maxc, span_obj

        if (
            nconds > 0
            and min(value_conds) == 0
            and all(_cond_is_integral(c) for c in value_conds)
        ):
            # Dense (gap-tolerant) 0..max integer cases -> SwitchIntegerWithDefault.
            # The exact 0..k-1 contiguous set is the no-gap special case. Holes route
            # to the default (the NONE target, or the exit index for a default-less
            # block -- both are the value a non-matching test already reaches, so the
            # fill is behavior-preserving). A density guard bounds the synthesized
            # table (matches lower._normalize_switch).
            #
            # All conds are finite integers here (the gate above rejects +-inf/NaN),
            # so max()/int() and the span are computed as Python ints and the density
            # guard is applied BEFORE any int32 narrowing: a set with a case >= 2^31
            # (or a sparse set) would overflow the cast, so it falls through to the
            # general SwitchWithDefault instead of crashing.
            maxc = int(max(value_conds))
            span_obj = maxc + 1
            # Gate-safe density guard: span+1 SwitchIntegerWithDefault target leaves
            # <= 2k+1 SwitchWithDefault leaves iff span <= 2k. Matches
            # lower._normalize_switch's guard so a normalized set is always filled.
            if span_obj <= 2 * nconds:
                span = <int32_t>span_obj  # bounded by 2*nconds, so the narrowing is safe
                if has_none:
                    default_val = outgoing[None]
                for ci in range(span):
                    args.append(self._int_leaf(outgoing.get(ci, default_val)))
                args.append(self._int_leaf(default_val))
                return self._intern_fn(_OP_SWITCH_INT_DEFAULT, args)

        # General case -> SwitchWithDefault(test, c0, t0, c1, t1, ..., default);
        # dict order is value cases ascending then None last.
        for cond, target in outgoing.items():
            if cond is None:
                default_val = target
                continue
            args.append(self._leaf_cond(cond))
            args.append(self._int_leaf(target))
        args.append(self._int_leaf(default_val))
        return self._intern_fn(_OP_SWITCH_DEFAULT, args)

    # -- block / whole-callback assembly -----------------------------------

    cdef object _emit_block(self, int32_t bid):
        cdef int32_t istart = self.func.blocks[bid].instr_start
        cdef int32_t icount = self.func.blocks[bid].instr_count
        cdef int32_t i
        cdef list statements = []
        for i in range(istart, istart + icount):
            if self.func.instrs[i].flags & <int32_t>FLAG_STMT_ROOT:
                statements.append(self._emit_stmt(i))
        statements.append(self._emit_terminator(bid))
        return self._intern_fn(_OP_EXECUTE, statements)

    cdef void _compute_block_map(self):
        # Finding #4: elide empty SHARED exit blocks (>= 2 predecessors, no statements,
        # no outgoing edges, not the entry). Such a block only emits Execute(exit) --
        # a one-hop bounce to the JumpLoop halt sentinel -- so its predecessors can
        # target the exit index directly. Emission-only: the CFG/export keep the block.
        # Single-predecessor empty exits are left in place (rare; keeps emission
        # deterministic and the emit unit tests stable). The exit index is recomputed
        # as the number of blocks actually emitted (== the trailing sentinel index).
        cdef int32_t nb = self.func.n_blocks
        cdef int32_t bid, e, i, istart, icount, c
        cdef bint has_stmt
        cdef list indeg = [0] * nb
        for e in range(self.func.n_edges):
            indeg[self.func.edges[e].dst] = <int32_t>indeg[self.func.edges[e].dst] + 1
        cdef list elided = [False] * nb
        for bid in range(nb):
            if bid == self.func.entry_block:
                continue
            if self.func.blocks[bid].edge_count != 0:
                continue
            if <int32_t>indeg[bid] < 2:
                continue
            istart = self.func.blocks[bid].instr_start
            icount = self.func.blocks[bid].instr_count
            has_stmt = False
            for i in range(istart, istart + icount):
                if self.func.instrs[i].flags & <int32_t>FLAG_STMT_ROOT:
                    has_stmt = True
                    break
            if not has_stmt:
                elided[bid] = True
        self._block_map = [0] * nb
        c = 0
        for bid in range(nb):
            if not <bint>elided[bid]:
                self._block_map[bid] = c
                c += 1
        self._exit_index = c
        for bid in range(nb):
            if <bint>elided[bid]:
                self._block_map[bid] = c  # collapse to the exit / halt sentinel

    cdef object run(self):
        cdef int32_t nb = self.func.n_blocks
        cdef int32_t bid
        self._compute_block_map()
        cdef list block_nodes = []
        for bid in range(nb):
            if <int32_t>self._block_map[bid] != self._exit_index:
                block_nodes.append(self._emit_block(bid))
        block_nodes.append(self._int_leaf(0))  # trailing exit sentinel at exit_index
        jumploop = self._intern_fn(_OP_JUMPLOOP, block_nodes)
        return self._intern_fn(_OP_BLOCK, [jumploop])


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

cdef object emit_func(Func func):
    """Emit an ``EngineNode`` tree from an arena ``Func`` (the fused-path entry)."""
    cdef _Emitter em = _Emitter(func)
    return em.run()


def emit_cfg(entry, mode=None, callback=None):
    """Marshal a Python ``BasicBlock`` CFG into the arena and emit its EngineNode.

    Non-destructive on ``entry``, so callers may run this before other consumers.
    """
    cdef Func func = Func()
    func._marshal(entry, mode, callback)
    return emit_func(func)
