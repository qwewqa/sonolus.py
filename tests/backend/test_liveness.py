"""Liveness analysis tests for the arena optimizer core (M1, §7.5).

Unit tests pin the subtle array/scalar/size-0/block-test rules directly against
``analysis.liveness_debug`` (name-set dicts keyed by arena block id). The wave-2
cross-checks against the now-deleted old ``LivenessAnalysis`` pass served their
purpose and are retired with M1. See OPTIMIZER_REWRITE.md §3 and §7.5.
"""

from __future__ import annotations

from sonolus.backend._opt import analysis  # noqa: PLC2701
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock
from sonolus.backend.place import BlockPlace, TempBlock


def _scalar(name):
    return BlockPlace(TempBlock(name, 1), 0, 0)


def _elem(arr, index, offset=0):
    return BlockPlace(arr, index, offset)


# --------------------------------------------------------------------------
# Liveness helper (new bitset liveness -> source-name sets).
# --------------------------------------------------------------------------

def _liveness(make_cfg, mode=None, callback=None):
    """Compute the new bitset liveness for a freshly built CFG (name-set dicts)."""
    return analysis.liveness_debug(make_cfg(), mode, callback)


# --------------------------------------------------------------------------
# Straight-line def/use.
# --------------------------------------------------------------------------

def test_straight_line_def_use():
    def make():
        b0 = BasicBlock()
        b0.statements = [
            IRSet(_scalar("a"), IRConst(1)),
            IRSet(_scalar("b"), IRPureInstr(Op.Add, [IRGet(_scalar("a")), IRConst(2)])),
            IRSet(BlockPlace(500, 0, 0), IRGet(_scalar("b"))),
        ]
        return b0

    d = _liveness(make)
    # a and b never simultaneously live (b defined where a dies).
    assert d["live_in"][0] == set()
    # per-statement live-out: a live after its def, b live after its def.
    live_sets = list(d["stmt_live"].values())
    assert {"a"} in live_sets
    assert {"b"} in live_sets


def test_undef_read_is_live_in():
    # Reading a never-written scalar keeps it live to block entry.
    def make():
        b0 = BasicBlock()
        b0.statements = [IRSet(BlockPlace(500, 0, 0), IRGet(_scalar("x")))]
        return b0

    d = _liveness(make)
    assert d["live_in"][0] == {"x"}


# --------------------------------------------------------------------------
# Loops.
# --------------------------------------------------------------------------

def test_loop_back_edge_keeps_temp_live():
    def make():
        b0 = BasicBlock()
        head = BasicBlock()
        ex = BasicBlock()
        b0.statements = [IRSet(_scalar("i"), IRConst(0))]
        b0.connect_to(head, None)
        head.statements = [IRSet(_scalar("i"), IRPureInstr(Op.Add, [IRGet(_scalar("i")), IRConst(1)]))]
        head.test = IRPureInstr(Op.Less, [IRGet(_scalar("i")), IRConst(10)])
        head.connect_to(head, None)  # loop back edge
        head.connect_to(ex, 0)
        ex.statements = [IRSet(BlockPlace(500, 0, 0), IRGet(_scalar("i")))]
        return b0

    d = _liveness(make)
    # i is live across the loop head (used and redefined each iteration).
    assert "i" in d["live_out"][1]
    assert "i" in d["live_in"][1]


# --------------------------------------------------------------------------
# Arrays.
# --------------------------------------------------------------------------

def test_array_first_write_kills_liveness():
    # arr[0]=1 is the first (and only) write => is_array_init => kills whole-array
    # liveness, so arr is not live at block entry.
    def make():
        arr = TempBlock("g", 4)
        b0 = BasicBlock()
        b0.statements = [
            IRSet(_elem(arr, 0), IRConst(1)),
            IRSet(_scalar("out"), IRGet(_elem(arr, 1))),
            IRSet(BlockPlace(600, 0, 0), IRGet(_scalar("out"))),
        ]
        return b0

    d = _liveness(make)
    assert "g" not in d["live_in"][0]
    # the array write is flagged init.
    assert all(d["is_array_init"].values())


def test_array_non_first_write_not_init():
    # Diamond: arr written on one path only; the join-block write is NOT the
    # first write on every path, so it does not kill liveness.
    def make():
        arr = TempBlock("g", 4)
        b0 = BasicBlock()
        b1 = BasicBlock()
        b2 = BasicBlock()
        b3 = BasicBlock()
        b0.test = IRGet(_scalar("c"))
        b0.connect_to(b1, 0)
        b0.connect_to(b2, None)
        b1.statements = [IRSet(_elem(arr, 0), IRConst(1))]  # write on one path
        b1.connect_to(b3, None)
        b2.connect_to(b3, None)  # no write on this path
        b3.statements = [
            IRSet(_elem(arr, 1), IRConst(2)),  # NOT init
            IRSet(BlockPlace(700, 0, 0), IRGet(_elem(arr, 2))),  # read arr
        ]
        return b0

    d = _liveness(make)
    # b1's write initializes; b3's write does not.
    inits = d["is_array_init"]
    assert list(inits.values()).count(True) == 1
    assert list(inits.values()).count(False) == 1
    # array_defs_out reaches b3 via the b1 path.
    assert d["array_defs_out"][3] == {"g"}


def test_array_read_makes_whole_array_live():
    def make():
        arr = TempBlock("g", 4)
        b0 = BasicBlock()
        b1 = BasicBlock()
        b0.statements = [IRSet(_elem(arr, 0), IRConst(9))]  # init write
        b0.connect_to(b1, None)
        b1.statements = [IRSet(BlockPlace(800, 0, 0), IRGet(_elem(arr, 3)))]  # read a different element
        return b0

    d = _liveness(make)
    # Reading any element makes the whole array live at b1 entry and b0 exit.
    assert "g" in d["live_in"][1]
    assert "g" in d["live_out"][0]


def test_array_never_written_keeps_read_liveness():
    # An array read with no write anywhere: the not-live-before-first-write
    # filter only applies to arrays that have at least one write, so a
    # never-written array keeps ordinary read-liveness back to entry. This
    # gives it interference edges so allocation cannot overlap it with live
    # temps (undefined reads then observe the -1.0 padding consistently).
    def make():
        arr = TempBlock("g", 4)
        b0 = BasicBlock()
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        b1.statements = [IRSet(BlockPlace(810, 0, 0), IRGet(_elem(arr, 0)))]
        return b0

    d = _liveness(make)
    assert "g" in d["live_in"][1]
    assert "g" in d["live_in"][0]
    assert d["array_defs_out"][0] == set()


def test_array_written_not_live_before_first_write():
    # An array that IS written somewhere is not live before its first write
    # (the original rule, unchanged for written arrays).
    def make():
        arr = TempBlock("g", 4)
        b0 = BasicBlock()
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        b1.statements = [
            IRSet(_elem(arr, 0), IRConst(7)),
            IRSet(BlockPlace(810, 0, 0), IRGet(_elem(arr, 0))),
        ]
        return b0

    d = _liveness(make)
    assert "g" not in d["live_in"][0]
    assert "g" not in d["live_in"][1]


def test_dynamic_array_index_reads_index_temp():
    def make():
        arr = TempBlock("g", 8)
        b0 = BasicBlock()
        b0.statements = [
            IRSet(_elem(arr, 0), IRConst(0)),  # init
            IRSet(_scalar("out"), IRGet(_elem(arr, IRGet(_scalar("k"))))),  # arr[k] read
            IRSet(BlockPlace(820, 0, 0), IRGet(_scalar("out"))),
        ]
        return b0

    d = _liveness(make)
    # k (the dynamic index) is live entering the block.
    assert "k" in d["live_in"][0]
    # arr is live where read but killed by the init write.
    assert "g" not in d["live_in"][0]


# --------------------------------------------------------------------------
# Block-test uses.
# --------------------------------------------------------------------------

def test_block_test_counts_as_use():
    def make():
        b0 = BasicBlock()
        t = BasicBlock()
        f = BasicBlock()
        b0.test = IRPureInstr(Op.Less, [IRGet(_scalar("x")), IRConst(3)])
        b0.connect_to(f, 0)
        b0.connect_to(t, None)
        t.statements = [IRSet(BlockPlace(900, 0, 0), IRConst(1))]
        f.statements = [IRSet(BlockPlace(900, 0, 0), IRConst(2))]
        return b0

    d = _liveness(make)
    # x is used only by the block test, so it is live at block entry.
    assert "x" in d["live_in"][0]


def test_test_use_propagates_to_predecessor_live_out():
    def make():
        b0 = BasicBlock()
        b1 = BasicBlock()
        t = BasicBlock()
        f = BasicBlock()
        b0.statements = [IRSet(_scalar("x"), IRConst(5))]
        b0.connect_to(b1, None)
        b1.test = IRGet(_scalar("x"))
        b1.connect_to(f, 0)
        b1.connect_to(t, None)
        t.statements = [IRSet(BlockPlace(910, 0, 0), IRConst(1))]
        f.statements = [IRSet(BlockPlace(910, 0, 0), IRConst(0))]
        return b0

    d = _liveness(make)
    # x is defined in b0, tested in b1: live-out of b0, live-in of b1.
    assert "x" in d["live_out"][0]
    assert "x" in d["live_in"][1]


# --------------------------------------------------------------------------
# Dead stores in the backward transfer (can_skip).
# --------------------------------------------------------------------------

def test_dead_store_does_not_keep_operands_live():
    # `a = b` where a is never used: the store is skipped, so b is not made live.
    def make():
        b0 = BasicBlock()
        b0.statements = [
            IRSet(_scalar("a"), IRGet(_scalar("b"))),  # a unused -> dead -> skipped
            IRSet(_scalar("c"), IRConst(9)),
            IRSet(BlockPlace(920, 0, 0), IRGet(_scalar("c"))),
        ]
        return b0

    d = _liveness(make)
    assert "b" not in d["live_in"][0]
    assert "a" not in d["live_in"][0]


def test_side_effecting_store_not_skipped():
    # `a = DebugLog(x)` with a unused: the value is side-effecting so the store
    # is NOT skipped -> x stays live.
    def make():
        b0 = BasicBlock()
        b0.statements = [IRSet(_scalar("a"), IRInstr(Op.DebugLog, [IRGet(_scalar("x"))]))]
        return b0

    d = _liveness(make)
    assert "x" in d["live_in"][0]


# --------------------------------------------------------------------------
# Size-0 temps.
# --------------------------------------------------------------------------

def test_size0_temp_write_is_never_a_def():
    # A size-0 temp is never a def: writing it (but never reading it) leaves it
    # out of every live set, since the write kills nothing and gens nothing.
    def make():
        e = TempBlock("e", 0)
        b0 = BasicBlock()
        b0.statements = [
            IRSet(BlockPlace(e, 0, 0), IRConst(0)),
            IRSet(BlockPlace(930, 0, 0), IRConst(1)),
        ]
        return b0

    d = _liveness(make)
    assert "e" not in d["live_in"][0]
    assert "e" not in d["live_out"][0]
    assert all("e" not in s for s in d["live_out"].values())
    assert all("e" not in s for s in d["stmt_live"].values())


def test_size0_temp_read_is_use_gen():
    # A size-0 temp is still an ordinary use for gen: reading a never-written
    # size-0 temp keeps it live back to block entry (like any read scalar).
    def make():
        e = TempBlock("e", 0)
        b0 = BasicBlock()
        b0.statements = [IRSet(BlockPlace(500, 0, 0), IRGet(BlockPlace(e, 0, 0)))]
        return b0

    d = _liveness(make)
    assert d["live_in"][0] == {"e"}
