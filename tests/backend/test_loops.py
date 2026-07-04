"""LoopForest unit tests (``analysis.loops_debug``).

Direct unit coverage of the loop forest (parent / loop_depth / innermost / body)
and the ``crosses_loop`` treeify query, mirroring the dominators tests in
test_ssa.py. Block ids are arena reverse-postorder ids; ``loops`` entries are
``(header, parent, loop_depth, sorted body block ids)``.
"""

from __future__ import annotations

from sonolus.backend._opt import analysis  # noqa: PLC2701
from sonolus.backend.ir import IRGet
from sonolus.backend.optimize.flow import BasicBlock
from sonolus.backend.place import BlockPlace, TempBlock


def _sc(name: str) -> BlockPlace:
    return BlockPlace(TempBlock(name, 1), 0, 0)


def _nested_two_loops() -> BasicBlock:
    # entry(0) -> outer(1); outer loops over inner(2) self-loop -> after_inner(3)
    # -> back to outer; outer exits to exit(4). Inner is a nested loop inside outer.
    entry = BasicBlock()
    outer = BasicBlock(test=IRGet(_sc("i")))
    inner = BasicBlock(test=IRGet(_sc("j")))
    after_inner = BasicBlock()
    exit_b = BasicBlock()
    entry.connect_to(outer, None)
    outer.connect_to(inner, None)  # enter the inner loop
    outer.connect_to(exit_b, 0)  # exit the outer loop
    inner.connect_to(inner, None)  # inner self back-edge
    inner.connect_to(after_inner, 0)
    after_inner.connect_to(outer, None)  # outer back-edge
    return entry


def _shared_header_two_latch() -> BasicBlock:
    # entry(0) -> h(1); h -> a(2); a -> h (latch 1); a -> b(3); b -> h (latch 2);
    # h -> exit(4). Two back-edges share the header h, so both latches belong to
    # ONE merged natural loop.
    entry = BasicBlock()
    h = BasicBlock(test=IRGet(_sc("c")))
    a = BasicBlock(test=IRGet(_sc("d")))
    b = BasicBlock()
    exit_b = BasicBlock()
    entry.connect_to(h, None)
    h.connect_to(a, None)
    h.connect_to(exit_b, 0)
    a.connect_to(h, 0)  # latch 1
    a.connect_to(b, None)
    b.connect_to(h, None)  # latch 2
    return entry


def test_nested_two_loops_forest():
    d = analysis.loops_debug(_nested_two_loops())
    assert d["n_loops"] == 2
    # (header, parent, loop_depth, body): outer is a top-level loop; inner nests in it.
    assert d["loops"] == [(1, -1, 1, [1, 2, 3]), (2, 0, 2, [2])]
    assert d["depth"] == {0: 0, 1: 1, 2: 2, 3: 1, 4: 0}
    assert d["innermost"] == {0: -1, 1: 0, 2: 1, 3: 0, 4: -1}


def test_shared_header_two_latch_merges_to_one_loop():
    d = analysis.loops_debug(_shared_header_two_latch())
    # Both back-edges target the same header, so there is a single merged loop
    # spanning the header, the mid block, and the second latch.
    assert d["n_loops"] == 1
    assert d["loops"] == [(1, -1, 1, [1, 2, 3])]


def test_crosses_loop_pinning():
    crosses = analysis.loops_debug(_nested_two_loops())["crosses"]
    # def before the loops, use inside the inner body -> crosses a loop boundary.
    assert crosses(0, 2) is True
    # def and use in the same innermost loop -> does not cross.
    assert crosses(2, 2) is False
    # neither endpoint is in any loop -> does not cross.
    assert crosses(0, 4) is False
