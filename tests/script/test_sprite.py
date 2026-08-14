"""Tests for what `Sprite.draw` and the six `Sprite.draw_curved_*` methods emit.

Two layers, because neither one alone covers the argument. `pad_z_indexes` is reachable through
run_and_validate, but the emitted `Op.Draw` is not: the interpreter does not model that op, so
neither run_and_validate nor run_compiled can observe the call at all, and the second layer walks
the finalized node tree instead, the way tests/script/test_array.py does for the sort fold.
"""

import random

import pytest

from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import STANDARD_PASSES, optimize_and_finalize
from sonolus.script.array import Array
from sonolus.script.internal.context import RuntimeChecks
from sonolus.script.quad import Rect
from sonolus.script.sprite import Sprite, pad_z_indexes
from sonolus.script.vec import Vec2
from tests.script.conftest import compile_fn, run_and_validate, run_compiled

QUAD = Rect(t=1, r=2, b=-3, l=-4)


@pytest.mark.parametrize(
    ("z", "expected"),
    [
        (7.0, (7, 0, 0, 0)),
        ((7.0,), (7, 0, 0, 0)),
        ((7.0, 11.0), (7, 11, 0, 0)),
        ((7.0, 11.0, 13.0), (7, 11, 13, 0)),
        ((7.0, 11.0, 13.0, 17.0), (7, 11, 13, 17)),
    ],
)
def test_pad_z_indexes(z, expected):
    # A z-index may be a single value or a tuple of up to four, and values that are not supplied are
    # treated as 0 (docs/concepts/resources.md). The Array wrapper is what gets the result past
    # run_and_validate, which cannot dereference a bare tuple.
    def fn():
        return Array(*pad_z_indexes(z))

    result = run_and_validate(fn)
    assert tuple(result) == expected


@pytest.mark.parametrize("z", [(), (1.0, 2.0, 3.0, 4.0, 5.0)])
@pytest.mark.parametrize("runtime_checks", list(RuntimeChecks))
def test_pad_z_indexes_rejects_invalid_tuple_length(z, runtime_checks):
    def fn():
        pad_z_indexes(z)
        return 23

    assert run_compiled(fn, runtime_checks=runtime_checks) == 0


@pytest.mark.parametrize("z", [(), (1.0, 2.0, 3.0, 4.0, 5.0)])
def test_invalid_z_index_in_runtime_dead_branch_compiles(z):
    def fn():
        if random.randrange(0, 1):
            pad_z_indexes(z)
        return 23

    # Unlike run_and_validate, the default run_compiled matrix covers every runtime-check setting.
    assert run_compiled(fn) == 23


DRAW_OPS = {
    Op.Draw,
    Op.DrawCurvedB,
    Op.DrawCurvedT,
    Op.DrawCurvedL,
    Op.DrawCurvedR,
    Op.DrawCurvedBT,
    Op.DrawCurvedLR,
}


def _draw_op(cb) -> tuple[Op, list[float]]:
    """Return the single drawing op the callback emits, with its arguments."""
    cfg, _rom = compile_fn(cb)
    node = optimize_and_finalize(cfg, STANDARD_PASSES)
    found = []

    def walk(n):
        if not isinstance(n, FunctionNode):
            return
        if n.func in DRAW_OPS:
            found.append((n.func, list(n.args)))
        for arg in n.args:
            walk(arg)

    walk(node)
    assert len(found) == 1, f"expected exactly one drawing op, got {[op for op, _ in found]}"
    return found[0]


def _draw_op_args(cb) -> list[float]:
    op, args = _draw_op(cb)
    assert op is Op.Draw
    return args


@pytest.mark.parametrize(
    ("z", "expected_tail"),
    [
        (7.0, [7, 0.25, 0, 0, 0]),
        ((7.0,), [7, 0.25, 0, 0, 0]),
        ((7.0, 11.0), [7, 0.25, 11, 0, 0]),
        ((7.0, 11.0, 13.0), [7, 0.25, 11, 13, 0]),
        ((7.0, 11.0, 13.0, 17.0), [7, 0.25, 11, 13, 17]),
    ],
)
def test_draw_emits_z_and_alpha_in_slot_order(z, expected_tail):
    # A regression pin, not an oracle: the expected `z1, a, z2, z3, z4` tail is read off the
    # parameter list of `_draw` in sonolus/script/sprite.py, since nothing in this repository states
    # the wire order of Op.Draw independently (ops.py declares no arity for it and interpret.py does
    # not model it). So this catches a future transposition among the slots, which the corpus cannot
    # see because every other call site leaves z2, z3 and z4 at 0, but it cannot catch an order that
    # is already wrong against the real runtime.
    # Distinct non-zero values are what make every transposition observable.
    def cb():
        Sprite(3).draw(QUAD, z=z, a=0.25)

    assert _draw_op_args(cb)[-5:] == expected_tail


# Every value below is distinct from every other and from the quad's coordinates, so any transposition
# among the slots (p against q, cp1 against cp2, n against a z) changes the argument list.
CURVE_Z = (7.0, 11.0, 13.0, 17.0)
CURVE_A = 0.25
CURVE_N = 5
CP1 = Vec2(0.5, 1.5)
CP2 = Vec2(2.5, 3.5)


def _cb_curved_b():
    Sprite(3).draw_curved_b(QUAD, CP1, CURVE_N, z=CURVE_Z, a=CURVE_A)


def _cb_curved_t():
    Sprite(3).draw_curved_t(QUAD, CP1, CURVE_N, z=CURVE_Z, a=CURVE_A)


def _cb_curved_l():
    Sprite(3).draw_curved_l(QUAD, CP1, CURVE_N, z=CURVE_Z, a=CURVE_A)


def _cb_curved_r():
    Sprite(3).draw_curved_r(QUAD, CP1, CURVE_N, z=CURVE_Z, a=CURVE_A)


def _cb_curved_bt():
    Sprite(3).draw_curved_bt(QUAD, CP1, CP2, CURVE_N, z=CURVE_Z, a=CURVE_A)


def _cb_curved_lr():
    Sprite(3).draw_curved_lr(QUAD, CP1, CP2, CURVE_N, z=CURVE_Z, a=CURVE_A)


@pytest.mark.parametrize(
    ("cb", "op", "expected_tail"),
    [
        (_cb_curved_b, Op.DrawCurvedB, [7, 0.25, 5, 0.5, 1.5, 11, 13, 17]),
        (_cb_curved_t, Op.DrawCurvedT, [7, 0.25, 5, 0.5, 1.5, 11, 13, 17]),
        (_cb_curved_l, Op.DrawCurvedL, [7, 0.25, 5, 0.5, 1.5, 11, 13, 17]),
        (_cb_curved_r, Op.DrawCurvedR, [7, 0.25, 5, 0.5, 1.5, 11, 13, 17]),
        (_cb_curved_bt, Op.DrawCurvedBT, [7, 0.25, 5, 0.5, 1.5, 2.5, 3.5, 11, 13, 17]),
        (_cb_curved_lr, Op.DrawCurvedLR, [7, 0.25, 5, 0.5, 1.5, 2.5, 3.5, 11, 13, 17]),
    ],
)
def test_each_curved_draw_method_emits_its_own_op(cb, op, expected_tail):
    # The op assertion is the load-bearing one: the four quadratic methods share a signature and so do the
    # two cubic ones, so a call routed to the wrong variant passes any argument check.
    #
    # The tail is the same kind of regression pin as test_draw_emits_z_and_alpha_in_slot_order: it is read
    # off the parameter lists in sonolus/script/sprite.py, so it catches a future transposition but cannot
    # catch an order that is already wrong against the real runtime.
    emitted_op, args = _draw_op(cb)

    assert emitted_op is op
    assert args[len(args) - len(expected_tail) :] == expected_tail

    # The sprite id and the flattened quad occupy the same leading slots as in Op.Draw, which pins them.
    def plain():
        Sprite(3).draw(QUAD)

    assert args[:9] == _draw_op_args(plain)[:9]
