"""Tests for the arena EngineNode emitter (``sonolus.backend._opt.emit``).

The emitter is a behaviour-preserving port of ``sonolus/backend/finalize.py``
(``cfg_to_engine_node``) operating on the flat ``Func`` arena, plus the one
deliberate OPTIMIZER_REWRITE.md 7.6 addition: it re-flattens associative left
spines (``Add``/``Multiply``/``Mod``/``Rem``) as it builds the tree.

Three layers of coverage:

1. ``test_corpus_*`` -- A/B against the *old* ``cfg_to_engine_node`` on the full
   pydori callback corpus, in both the n-ary optimized form (a) and a binary
   ("raw"-like) form produced by ``UnflattenAssociativeOps`` (b). See the module
   docstring's "TWO DOCUMENTED DIVERGENCES" below for why the comparison is exact
   *modulo* two precisely-characterised, semantics-preserving normalisations.
2. ``test_*`` unit tests -- every terminator form, NaN/+-Inf/-0.0 constant
   lowering, pointer-deref nested ``Get``s, offset-folding branches, and n-ary
   flatten idempotence (incl. non-flattening of right-nested trees).
3. ``test_semantic_*`` -- hand-built CFGs run through *both* emit paths and the
   ``Interpreter`` oracle, asserting identical results / logs / memory (incl. a
   shared-subtree case proving hash-consing does not change evaluated semantics).

================================================================================
TWO DOCUMENTED DIVERGENCES (root-caused, semantics-preserving)
================================================================================
On the pydori corpus ``emit_cfg`` is byte-for-byte identical to old ``finalize``
for 244/300 callbacks; the other 56 differ for two reasons that are both
by-design and both preserve semantics exactly (identical evaluated results and
memory addresses). They are accounted for by the ``_normalize`` canonicalisation
applied symmetrically to both sides, never papered over:

* **Constant-index folding.** Old ``finalize``'s ``_block_place_to_engine_node``
  emits ``Get(block, Add(index, offset))`` even when ``index`` is a *constant*
  integer -- e.g. ``Set(4001, Add(14, 1), ...)``. The committed arena
  ``marshal_in`` (ir.pyx) folds a constant integer index into the place
  ``offset`` (``index_val == -1``), so ``emit_cfg`` emits the pre-folded
  ``Set(4001, 15, ...)``. ``Add(14, 1) == 15`` -- identical address, one fewer
  node. ir.pyx is a committed keystone this module may not modify, so the split
  is not recoverable; ``_normalize`` folds ``Add(<num>, <num>, ...)`` -> its
  order-preserving sum on both sides to compare.
* **Associative left-spine re-flattening (the 7.6 addition).** ``emit_cfg``
  re-flattens *every* associative (``Add``/``Multiply``/``Mod``/``Rem``) left
  spine it produces, whereas old ``finalize`` flattens none itself. Old only
  looks flat because the pipeline's ``FlattenAssociativeOps`` pre-flattened
  *value* expressions in the IR -- but that pass (a) leaves a stray left-nested
  ``Multiply(Multiply(a, b), c)`` on 2 callbacks where a later pass reintroduced
  it, and (b) never touches place *indices* at all, so old emits binary
  ``Get(block, Add(Add(p, q), off))`` where new emits flat ``Add(p, q, off)``.
  Left-spine reassociation preserves left-to-right FP order (``(a*b)*c`` either
  way), so it is semantics-preserving; ``_normalize`` flattens the *old* side's
  associative left spines (``flatten_spines=True``) to compare. new is never
  re-flattened by ``_normalize`` (it is already flat), so a flattening bug in
  ``emit_cfg`` -- over- or under-flattening -- still surfaces as a mismatch.

Category breakdown of the 56: 26 reconciled by constant-index folding alone,
30 also involving associative re-flattening; 300/300 after accounting for both.
"""

from __future__ import annotations

import math
from itertools import starmap

import pytest

from sonolus.backend._opt import emit  # noqa: PLC2701
from sonolus.backend.finalize import cfg_to_engine_node
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.node import FunctionNode, format_engine_node
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_reverse_postorder
from sonolus.backend.optimize.optimize import STANDARD_PASSES
from sonolus.backend.optimize.passes import OptimizerConfig, run_passes
from sonolus.backend.optimize.simplify import FlattenAssociativeOps, UnflattenAssociativeOps
from sonolus.backend.place import BlockPlace
from tests.backend.test_corpus_roundtrip import _MODE_SETUP, _iter_callbacks

_FLATTEN_OPS = frozenset({Op.Add, Op.Multiply, Op.Mod, Op.Rem})


# ---------------------------------------------------------------------------
# Structural comparison helpers (iterative -- no deep Python recursion).
# ---------------------------------------------------------------------------


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _normalize(root, flatten_spines: bool):
    """Canonicalise a node tree for A/B comparison (iterative, DAG-safe).

    Always folds a fully-numeric ``Add(<num>, <num>, ...)`` into its
    order-preserving sum (accounts for the constant-index divergence). When
    ``flatten_spines`` is set, also flattens associative left spines the way the
    7.6 emission addition does (accounts for the residual-reflatten divergence).
    ``flatten_spines`` is used only on the *old*-finalize side, so a flattening
    *bug* in ``emit_cfg`` (over- or under-flattening) still surfaces as a
    mismatch rather than being masked.
    """
    if not isinstance(root, FunctionNode):
        return root
    done: dict[int, object] = {}
    stack: list[tuple[object, bool]] = [(root, False)]
    while stack:
        node, processed = stack.pop()
        if not isinstance(node, FunctionNode) or id(node) in done:
            continue
        if not processed:
            stack.append((node, True))
            stack.extend((a, False) for a in node.args if isinstance(a, FunctionNode))
            continue
        new_args = [done[id(a)] if isinstance(a, FunctionNode) else a for a in node.args]
        op = node.func
        if (
            flatten_spines
            and op in _FLATTEN_OPS
            and new_args
            and isinstance(new_args[0], FunctionNode)
            and new_args[0].func == op
        ):
            new_args = list(new_args[0].args) + new_args[1:]
        if op == Op.Add and len(new_args) >= 2 and all(_is_num(a) for a in new_args):
            acc = new_args[0]
            for a in new_args[1:]:
                acc += a
            done[id(node)] = acc
        else:
            done[id(node)] = FunctionNode(op, tuple(new_args))
    return done[id(root)]


def _diff(a, b) -> str | None:
    """Return ``None`` if two node trees are structurally identical, else a path.

    Iterative (explicit stack) so arbitrarily deep corpus trees never blow the
    Python recursion limit. Leaves compare by exact type+value, so ``5`` and
    ``5.0`` (distinct ``SwitchWithDefault`` case labels) never compare equal.
    """
    stack = [(a, b, "root")]
    while stack:
        x, y, p = stack.pop()
        xf = isinstance(x, FunctionNode)
        yf = isinstance(y, FunctionNode)
        if xf != yf:
            return f"{p}: {type(x).__name__}={x!r} vs {type(y).__name__}={y!r}"
        if xf:
            if x.func != y.func:
                return f"{p}: func {x.func} vs {y.func}"
            if len(x.args) != len(y.args):
                return f"{p}: nargs {len(x.args)} vs {len(y.args)}"
            for i, (xa, ya) in enumerate(zip(x.args, y.args, strict=True)):
                stack.append((xa, ya, f"{p}.{x.func.name}[{i}]"))
        elif type(x) is not type(y) or x != y:
            return f"{p}: leaf {x!r} vs {y!r}"
    return None


def _assert_ab(new_node, old_node, *, flatten_old: bool, label: str):
    d = _diff(_normalize(new_node, flatten_spines=False), _normalize(old_node, flatten_spines=flatten_old))
    assert d is None, f"{label}: {d}"


# ---------------------------------------------------------------------------
# 1. A/B against old finalize on the full pydori corpus (both forms).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", list(_MODE_SETUP))
def test_corpus_ab_against_finalize(mode):
    """emit_cfg == old finalize on every pydori callback, both corpus forms."""
    exact_a = total = 0
    for label, cb, factory in _iter_callbacks(mode):
        total += 1

        # Form (a): the n-ary STANDARD-optimized CFG. emit_cfg is non-destructive
        # so it must run BEFORE old finalize, which deletes block attributes.
        opt = run_passes(factory(), STANDARD_PASSES, OptimizerConfig(mode=mode, callback=cb))
        new_a = emit.emit_cfg(opt, mode, cb)
        old_a = cfg_to_engine_node(opt)
        if _diff(new_a, old_a) is None:
            exact_a += 1
        _assert_ab(new_a, old_a, flatten_old=True, label=f"{label} form(a)")

        # Form (b): a binary "raw"-like CFG (UnflattenAssociativeOps), compared to
        # old finalize on a FlattenAssociativeOps-processed copy. emit_cfg must
        # re-flatten the binary spines to match.
        opt2 = run_passes(factory(), STANDARD_PASSES, OptimizerConfig(mode=mode, callback=cb))
        UnflattenAssociativeOps().run(opt2, OptimizerConfig())
        new_b = emit.emit_cfg(opt2, mode, cb)
        FlattenAssociativeOps().run(opt2, OptimizerConfig())
        old_b = cfg_to_engine_node(opt2)
        # flatten_old handles both residual value spines and place-index spines,
        # which old finalize never flattens (FlattenAssociativeOps skips places).
        _assert_ab(new_b, old_b, flatten_old=True, label=f"{label} form(b)")

    assert total > 0, f"no callbacks enumerated for {mode}"
    # Informational: raw byte-identical matches vs the documented divergences.
    print(f"\n[{mode.name}] form(a) raw-exact finalize match: {exact_a}/{total}")


# ---------------------------------------------------------------------------
# Unit-test scaffolding.
# ---------------------------------------------------------------------------


def _emit_program(entry, mode=None, cb=None):
    """emit_cfg(entry) -> (node, [Execute per block in RPO], {block: rpo index})."""
    node = emit.emit_cfg(entry, mode, cb)
    assert node.func == Op.Block
    jl = node.args[0]
    assert jl.func == Op.JumpLoop
    assert jl.args[-1] == 0  # trailing sentinel
    assert type(jl.args[-1]) is int
    idx = {b: i for i, b in enumerate(traverse_cfg_reverse_postorder(entry))}
    return node, list(jl.args[:-1]), idx


def _term(execute):
    """The terminator node (last arg of an Execute)."""
    assert execute.func == Op.Execute
    return execute.args[-1]


def _one_block(statements=None, test=None, outgoing=()):
    """A single-block CFG whose block-0 outgoing edges are ``(cond, dst)`` pairs."""
    b = BasicBlock(statements=list(statements or []), test=test)
    dsts = {}
    for cond, key in outgoing:
        dst = dsts.setdefault(key, BasicBlock())
        b.connect_to(dst, cond)
    return b


# ---------------------------------------------------------------------------
# 2a. Terminator forms.
# ---------------------------------------------------------------------------


def test_terminator_empty_exit():
    # {} -> constant exit index (== number of blocks).
    b0 = BasicBlock()
    _node, executes, _idx = _emit_program(b0)
    assert _term(executes[0]) == 1  # single block, exit index 1
    assert type(_term(executes[0])) is int


def test_terminator_unconditional():
    # {None: t} -> constant target index.
    b0, b1 = BasicBlock(), BasicBlock()
    b0.connect_to(b1, None)
    _node, executes, idx = _emit_program(b0)
    assert _term(executes[idx[b0]]) == idx[b1]
    assert _term(executes[idx[b1]]) == 2  # b1 is empty exit -> exit index 2


def test_terminator_if_zero_none():
    # {0: false, None: true} -> If(test, TRUE=none-edge, FALSE=zero-edge).
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    bt, bf = BasicBlock(), BasicBlock()
    b0.connect_to(bf, 0)
    b0.connect_to(bt, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.If
    assert t.args[0] == FunctionNode(Op.Get, (500, 0))  # test
    assert t.args[1] == idx[bt]  # TRUE branch = None edge
    assert t.args[2] == idx[bf]  # FALSE branch = 0 edge


def test_terminator_if_equal_const():
    # {None: default, c: branch} with c != 0 -> If(Equal(test, c), branch, default).
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    bc, bd = BasicBlock(), BasicBlock()
    b0.connect_to(bc, 5)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.If
    assert t.args[0] == FunctionNode(Op.Equal, (FunctionNode(Op.Get, (500, 0)), 5))
    assert t.args[1] == idx[bc]
    assert t.args[2] == idx[bd]


def test_terminator_switch_integer_with_default():
    # Contiguous 0..k-1 cases + default -> SwitchIntegerWithDefault.
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_cases = [BasicBlock() for _ in range(3)]
    bd = BasicBlock()
    for i, bc in enumerate(b_cases):
        b0.connect_to(bc, i)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchIntegerWithDefault
    assert t.args[0] == FunctionNode(Op.Get, (500, 0))
    assert list(t.args[1:4]) == [idx[bc] for bc in b_cases]
    assert t.args[4] == idx[bd]


def test_terminator_switch_integer_default_less():
    # Default-less contiguous cases {0, 1} -> SwitchIntegerWithDefault, default = exit.
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_cases = [BasicBlock(), BasicBlock()]
    for i, bc in enumerate(b_cases):
        b0.connect_to(bc, i)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchIntegerWithDefault
    assert list(t.args[1:3]) == [idx[bc] for bc in b_cases]
    assert t.args[3] == 3  # missing default -> exit index (3 blocks)


def test_terminator_switch_with_default_gap():
    # Gap in case values (0, 2) breaks contiguity -> SwitchWithDefault.
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_a, b_c, bd = BasicBlock(), BasicBlock(), BasicBlock()
    b0.connect_to(b_a, 0)
    b0.connect_to(b_c, 2)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchWithDefault
    assert t.args[0] == FunctionNode(Op.Get, (500, 0))
    assert list(t.args[1:5]) == [0, idx[b_a], 2, idx[b_c]]
    assert t.args[5] == idx[bd]


def test_terminator_switch_with_default_nonzero_min():
    # Non-zero minimum case (1, 2) breaks contiguity -> SwitchWithDefault.
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b1, b2, bd = BasicBlock(), BasicBlock(), BasicBlock()
    b0.connect_to(b1, 1)
    b0.connect_to(b2, 2)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchWithDefault
    assert list(t.args[1:5]) == [1, idx[b1], 2, idx[b2]]
    assert t.args[5] == idx[bd]


def test_terminator_switch_with_default_non_integral():
    # A non-integral case (1.5) breaks contiguity -> SwitchWithDefault; the float
    # label keeps its float display form (distinct from an int).
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_a, b_b, bd = BasicBlock(), BasicBlock(), BasicBlock()
    b0.connect_to(b_a, 0)
    b0.connect_to(b_b, 1.5)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchWithDefault
    # Ascending case order; 0 is int, 1.5 is a float.
    assert t.args[1] == 0
    assert type(t.args[1]) is int
    assert t.args[2] == idx[b_a]
    assert t.args[3] == 1.5
    assert type(t.args[3]) is float
    assert t.args[4] == idx[b_b]
    assert t.args[5] == idx[bd]


def test_terminator_switch_with_default_default_less():
    # Default-less non-contiguous multiway -> SwitchWithDefault, default = exit.
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_a, b_c = BasicBlock(), BasicBlock()
    b0.connect_to(b_a, 0)
    b0.connect_to(b_c, 2)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchWithDefault
    assert list(t.args[1:5]) == [0, idx[b_a], 2, idx[b_c]]
    assert t.args[5] == 3  # exit index (3 blocks), no default edge


# ---------------------------------------------------------------------------
# 2b. Constant lowering (int demotion, NaN / +-Inf via ROM, -0.0 -> int 0).
# ---------------------------------------------------------------------------


def _set_value_node(value_ir):
    """Emit ``Set(place, value_ir)`` and return the emitted value node (arg 2)."""
    b0 = BasicBlock(statements=[IRSet(BlockPlace(500, 0, 0), value_ir)])
    _node, executes, _idx = _emit_program(b0)
    set_node = executes[0].args[0]
    assert set_node.func == Op.Set
    return set_node.args[2]


def test_const_integral_float_demotes_to_int():
    v = _set_value_node(IRConst(5.0))
    assert v == 5
    assert type(v) is int


def test_const_finite_non_integral_stays_float():
    v = _set_value_node(IRConst(2.5))
    assert v == 2.5
    assert type(v) is float


def test_const_negative_zero_emits_int_zero():
    # Bit-level: -0.0 is integral, so it demotes to *int* 0 (matches old behavior).
    v = _set_value_node(IRConst(-0.0))
    assert v == 0
    assert type(v) is int


def test_const_positive_infinity_rom_read():
    v = _set_value_node(IRConst(math.inf))
    assert v == FunctionNode(Op.Get, (3000, 1))


def test_const_negative_infinity_rom_read():
    v = _set_value_node(IRConst(-math.inf))
    assert v == FunctionNode(Op.Get, (3000, 2))


def test_const_nan_rom_read():
    v = _set_value_node(IRConst(math.nan))
    assert v == FunctionNode(Op.Get, (3000, 0))


# ---------------------------------------------------------------------------
# 2c. Place emission: offset folding and pointer-deref nested Gets.
# ---------------------------------------------------------------------------


def _set_target_place_node(place):
    """Emit ``Set(place, 0)`` and return ``(block_node, index_node)``."""
    b0 = BasicBlock(statements=[IRSet(place, IRConst(0))])
    _node, executes, _idx = _emit_program(b0)
    set_node = executes[0].args[0]
    assert set_node.func == Op.Set
    return set_node.args[0], set_node.args[1]


def test_place_offset_zero_index_dynamic():
    # offset == 0, dynamic index -> Get(block, emit(index)).
    block, index = _set_target_place_node(BlockPlace(500, IRGet(BlockPlace(501, 0, 0)), 0))
    assert block == 500
    assert index == FunctionNode(Op.Get, (501, 0))


def test_place_offset_nonzero_index_zero():
    # offset != 0, constant index 0 -> raw int offset.
    block, index = _set_target_place_node(BlockPlace(500, 0, 7))
    assert block == 500
    assert index == 7
    assert type(index) is int


def test_place_offset_nonzero_index_dynamic():
    # offset != 0, dynamic index -> Add(emit(index), offset).
    block, index = _set_target_place_node(BlockPlace(500, IRGet(BlockPlace(501, 0, 0)), 7))
    assert block == 500
    assert index == FunctionNode(Op.Add, (FunctionNode(Op.Get, (501, 0)), 7))


def test_place_pointer_deref_nested_gets():
    # A place whose block is itself a place -> nested Get for the block node.
    place = BlockPlace(BlockPlace(600, 3, 0), 5, 0)
    b0 = BasicBlock(statements=[IRSet(BlockPlace(500, 0, 0), IRGet(place))])
    _node, executes, _idx = _emit_program(b0)
    value = executes[0].args[0].args[2]
    assert value == FunctionNode(Op.Get, (FunctionNode(Op.Get, (600, 3)), 5))


# ---------------------------------------------------------------------------
# 2d. n-ary re-flattening (the 7.6 addition): idempotence + right-nesting kept.
# ---------------------------------------------------------------------------


def _reads(*offsets):
    return [IRGet(BlockPlace(500, o, 0)) for o in offsets]


def test_flatten_deep_left_spine():
    a, b, c, d = _reads(0, 1, 2, 3)
    expr = IRPureInstr(Op.Add, [IRPureInstr(Op.Add, [IRPureInstr(Op.Add, [a, b]), c]), d])
    v = _set_value_node(expr)
    assert v.func == Op.Add
    assert list(v.args) == [
        FunctionNode(Op.Get, (500, 0)),
        FunctionNode(Op.Get, (500, 1)),
        FunctionNode(Op.Get, (500, 2)),
        FunctionNode(Op.Get, (500, 3)),
    ]


def test_flatten_already_nary_idempotent():
    # An already-n-ary Add (binarised by marshal-in) re-flattens to the same tree.
    a, b, c = _reads(0, 1, 2)
    v = _set_value_node(IRPureInstr(Op.Add, [a, b, c]))
    assert v.func == Op.Add
    assert list(v.args) == [
        FunctionNode(Op.Get, (500, 0)),
        FunctionNode(Op.Get, (500, 1)),
        FunctionNode(Op.Get, (500, 2)),
    ]


def test_flatten_right_nested_not_flattened():
    # Add(a, Add(b, c)) must NOT flatten (would change FP evaluation order).
    a, b, c = _reads(0, 1, 2)
    v = _set_value_node(IRPureInstr(Op.Add, [a, IRPureInstr(Op.Add, [b, c])]))
    assert v.func == Op.Add
    assert len(v.args) == 2
    assert v.args[0] == FunctionNode(Op.Get, (500, 0))
    assert v.args[1] == FunctionNode(Op.Add, (FunctionNode(Op.Get, (500, 1)), FunctionNode(Op.Get, (500, 2))))


def test_flatten_descends_into_effectful_args():
    # FlattenAssociativeOps descends into impure-instr args; so does emit. The
    # Add spine inside a Draw's args must flatten while Draw stays as-is.
    a, b, c = _reads(0, 1, 2)
    add = IRPureInstr(Op.Add, [IRPureInstr(Op.Add, [a, b]), c])
    b0 = BasicBlock(statements=[IRInstr(Op.DebugLog, [add])])
    _node, executes, _idx = _emit_program(b0)
    log = executes[0].args[0]
    assert log.func == Op.DebugLog
    assert log.args[0].func == Op.Add
    assert len(log.args[0].args) == 3


def test_flatten_multiply_left_spine():
    a, b, c = _reads(0, 1, 2)
    v = _set_value_node(IRPureInstr(Op.Multiply, [IRPureInstr(Op.Multiply, [a, b]), c]))
    assert v.func == Op.Multiply
    assert len(v.args) == 3


# ---------------------------------------------------------------------------
# 2e. Hash-consing: structurally equal subtrees become the same object.
# ---------------------------------------------------------------------------


def test_hash_consing_shares_equal_subtrees():
    # Two structurally identical reads in one expression collapse to one object.
    r0 = IRGet(BlockPlace(500, 0, 0))
    v = _set_value_node(IRPureInstr(Op.Add, [r0, IRGet(BlockPlace(500, 0, 0))]))
    assert v.func == Op.Add
    assert len(v.args) == 2
    assert v.args[0] is v.args[1]  # same FunctionNode object


def test_hash_consing_distinct_when_different():
    v = _set_value_node(IRPureInstr(Op.Add, _reads(0, 1)))
    assert v.args[0] is not v.args[1]


# ---------------------------------------------------------------------------
# 3. Semantic parity through the Interpreter (both emit paths + hash-consing).
# ---------------------------------------------------------------------------


def _interpret(node):
    it = Interpreter()
    it.blocks[3000] = [math.nan, math.inf, -math.inf]  # ROM: NaN, +Inf, -Inf
    it.run(node)
    return it


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b


def _column_equal(v1: list, v2: list) -> bool:
    return len(v1) == len(v2) and all(starmap(_same, zip(v1, v2, strict=True)))


def _mem_equal(m1: dict, m2: dict) -> bool:
    return m1.keys() == m2.keys() and all(_column_equal(m1[k], m2[k]) for k in m1)


def _assert_semantic_parity(build):
    """Build the CFG twice; emit new + old; assert identical interpretation."""
    new_it = _interpret(emit.emit_cfg(build()))
    old_it = _interpret(cfg_to_engine_node(build()))
    assert new_it.log == old_it.log
    assert _mem_equal(new_it.blocks, old_it.blocks)
    return new_it


def test_semantic_values_specials_and_shared_subtree():
    def build():
        b0 = BasicBlock()
        b1 = BasicBlock()
        shared = IRGet(BlockPlace(500, 0, 0))
        b0.statements = [
            IRSet(BlockPlace(500, 0, 0), IRConst(7)),
            # 7 + 7 via a shared read subtree (hash-consing collapses it).
            IRSet(BlockPlace(500, 1, 0), IRPureInstr(Op.Add, [shared, IRGet(BlockPlace(500, 0, 0))])),
            IRInstr(Op.DebugLog, [IRGet(BlockPlace(500, 1, 0))]),
            IRSet(BlockPlace(500, 2, 0), IRConst(math.inf)),  # via ROM
            IRSet(BlockPlace(500, 3, 0), IRConst(math.nan)),  # via ROM
        ]
        b0.connect_to(b1, None)
        return b0

    it = _assert_semantic_parity(build)
    assert it.log == [14.0]
    assert it.blocks[500][0] == 7
    assert it.blocks[500][1] == 14
    assert math.isinf(it.blocks[500][2])
    assert it.blocks[500][2] > 0
    assert math.isnan(it.blocks[500][3])

    # The shared subtree really is one object in the emitted tree.
    node = emit.emit_cfg(build())
    add = node.args[0].args[0].args[1].args[2]  # Block>JumpLoop>Execute>Set>value
    assert add.func == Op.Add
    assert add.args[0] is add.args[1]


def test_semantic_if_branch_both_directions():
    def build_for(x):
        def build():
            b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
            bt, bf, exit_b = BasicBlock(), BasicBlock(), BasicBlock()
            b0.statements = [IRSet(BlockPlace(500, 0, 0), IRConst(x))]
            bt.statements = [IRInstr(Op.DebugLog, [IRConst(111)])]
            bf.statements = [IRInstr(Op.DebugLog, [IRConst(222)])]
            b0.connect_to(bf, 0)
            b0.connect_to(bt, None)
            bt.connect_to(exit_b, None)
            bf.connect_to(exit_b, None)
            return b0

        return build

    _assert_semantic_parity(build_for(0))  # false branch
    _assert_semantic_parity(build_for(1))  # true branch


def test_semantic_switch_dispatch():
    def build_for(sel):
        def build():
            b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
            cases = [BasicBlock() for _ in range(3)]
            bd, exit_b = BasicBlock(), BasicBlock()
            b0.statements = [IRSet(BlockPlace(500, 0, 0), IRConst(sel))]
            for i, bc in enumerate(cases):
                bc.statements = [IRInstr(Op.DebugLog, [IRConst(10 + i)])]
                b0.connect_to(bc, i)
                bc.connect_to(exit_b, None)
            bd.statements = [IRInstr(Op.DebugLog, [IRConst(99)])]
            b0.connect_to(bd, None)
            bd.connect_to(exit_b, None)
            return b0

        return build

    for sel in (0, 1, 2, 5):  # 5 falls through to default
        _assert_semantic_parity(build_for(sel))


def test_semantic_pointer_deref():
    def build():
        b0, b1 = BasicBlock(), BasicBlock()
        b0.statements = [
            IRSet(BlockPlace(600, 3, 0), IRConst(500)),  # mem[600][3] = block id 500
            IRSet(BlockPlace(500, 5, 0), IRConst(42)),  # mem[500][5] = 42
            # Read mem[mem[600][3]][5] via a pointer-deref place -> Get(Get(600,3),5)
            IRInstr(Op.DebugLog, [IRGet(BlockPlace(BlockPlace(600, 3, 0), 5, 0))]),
        ]
        b0.connect_to(b1, None)
        return b0

    it = _assert_semantic_parity(build)
    assert it.log == [42.0]


def test_semantic_nary_flatten_preserves_result():
    def build():
        b0, b1 = BasicBlock(), BasicBlock()
        reads = [IRGet(BlockPlace(500, i, 0)) for i in range(4)]
        spine = reads[0]
        for r in reads[1:]:
            spine = IRPureInstr(Op.Add, [spine, r])
        b0.statements = [
            IRSet(BlockPlace(500, 0, 0), IRConst(1)),
            IRSet(BlockPlace(500, 1, 0), IRConst(2)),
            IRSet(BlockPlace(500, 2, 0), IRConst(4)),
            IRSet(BlockPlace(500, 3, 0), IRConst(8)),
            IRInstr(Op.DebugLog, [spine]),
        ]
        b0.connect_to(b1, None)
        return b0

    it = _assert_semantic_parity(build)
    assert it.log == [15.0]
    # new emit flattens the spine; result is unchanged.
    node = emit.emit_cfg(build())
    add = node.args[0].args[0].args[4].args[0]
    assert add.func == Op.Add
    assert len(add.args) == 4


def test_format_engine_node_smoke():
    # A quick end-to-end format check (readability of the emitted tree).
    b0, b1 = BasicBlock(), BasicBlock()
    b0.statements = [IRSet(BlockPlace(500, 0, 0), IRConst(3))]
    b0.connect_to(b1, None)
    text = format_engine_node(emit.emit_cfg(b0))
    assert text.startswith("Block(")
    assert "JumpLoop" in text
    assert "Execute" in text
