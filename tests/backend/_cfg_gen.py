"""Hypothesis generator of small, legal, terminating, deterministic CFG programs.

Mirrors the shallow three-address CFGs the frontend emits (OPTIMIZER_REWRITE.md
section 3): scalar/array ``TempBlock`` virtual registers, plain int memory blocks
for observable IO, structured control flow (sequences, if-diamonds, multi-way
switches with and without a default edge, and bounded counting loops), and the
foldable pure-op vocabulary of section 2.

A generated program is an immutable *recipe* (a tree of frozen dataclasses drawn
once by Hypothesis). ``build_cfg(recipe)`` lowers a recipe into a *fresh*
``BasicBlock`` CFG on every call, so the differential tests can rebuild an
identical program as many times as they need (``run_passes`` /
``cfg_to_engine_node`` may consume their input).

Safety invariants that keep the oracle (``sonolus.backend.interpret``) from ever
raising, so that any cross-level disagreement is a real miscompile and not a
generator artefact:

* Every store into a temp (scalar or array) and every value loaded out of an
  observable block is clamped into ``[-BOUND, BOUND]`` via ``Min(Max(...))``.
  That keeps all temp values finite (no ``Inf`` from a loop-carried ``Multiply``
  or Fibonacci-style ``Add``), so ``Floor``/``Round`` never overflow.
* ``Divide``/``Mod`` always divide by a non-zero constant (no ``ZeroDivision``).
* Dynamic array indexes are ``Mod(Floor(Abs(x)), size)`` (or ``Mod(counter,
  size)`` inside a loop) -- always a non-negative in-bounds integer, even when
  ``x`` is an undefined temp read (which yields ``-1.0``).
* Loop counters are dedicated integer temps, initialised to 0 and incremented by
  exactly 1 per iteration, tested ``counter < bound`` with ``bound <= 8`` -- so
  every loop terminates by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hypothesis import strategies as st

from sonolus.backend.ir import IRConst, IRExpr, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_reverse_postorder
from sonolus.backend.place import BlockPlace, TempBlock

# Two plain int memory blocks used for observable Set/Get (never allocated to,
# always block ids 20/21 -- distinct from the temp arena block 10000).
OBS_BLOCKS: tuple[int, int] = (20, 21)
OBS_SLOTS = 6  # observable slot index range [0, OBS_SLOTS)
OBS_CAPTURE_LEN = 12  # how many slots per observable block to read back

# Magnitude clamp on every temp store, keeping all values finite.
BOUND = 1000.0

# Non-zero divisors for Divide/Mod (avoids oracle ZeroDivisionError).
_NONZERO = (-8, -5, -3, -2, -1, 1, 2, 3, 4, 7)

# Small constant leaves: integers plus a few non-integers so Floor/Round/Divide
# have something to bite on and -0.0 drift can surface through Negate(0).
_CONSTS = (*range(-8, 9), 0.5, -0.5, 1.5, 2.5, -1.5, 3.25)

_BINARY_OPS = (Op.Add, Op.Subtract, Op.Multiply, Op.Min, Op.Max, Op.Equal, Op.Less, Op.And, Op.Or)
_GUARDED_BINARY_OPS = (Op.Divide, Op.Mod)  # rhs forced to a non-zero constant
_UNARY_OPS = (Op.Abs, Op.Floor, Op.Round, Op.Negate, Op.Not)


# ==========================================================================
# Recipe data model (immutable; drawn once, lowered many times).
# ==========================================================================


@dataclass(frozen=True)
class Const:
    value: float


@dataclass(frozen=True)
class Read:
    temp: int  # scalar temp id in [0, n_scalars)


@dataclass(frozen=True)
class ReadArr:
    arr: int  # array id
    index: int  # constant index in [0, size)


@dataclass(frozen=True)
class Unary:
    op: Op
    a: object


@dataclass(frozen=True)
class Binary:
    op: Op
    a: object
    b: object


@dataclass(frozen=True)
class SetScalar:
    dst: int
    expr: object


@dataclass(frozen=True)
class SetArrConst:
    arr: int
    index: int
    expr: object


@dataclass(frozen=True)
class SetArrDyn:
    arr: int
    src: int  # scalar temp id used to derive the (fallback) dynamic index
    expr: object


@dataclass(frozen=True)
class LoadArrDyn:
    dst: int
    arr: int
    src: int


@dataclass(frozen=True)
class SetObs:
    block: int  # 0 or 1 -> OBS_BLOCKS
    index: int
    expr: object


@dataclass(frozen=True)
class LoadObs:
    dst: int
    block: int
    index: int


@dataclass(frozen=True)
class Log:
    expr: object


@dataclass(frozen=True)
class BlockNode:
    stmts: tuple


@dataclass(frozen=True)
class Seq:
    nodes: tuple


@dataclass(frozen=True)
class If:
    test: object
    then_: object
    else_: object


@dataclass(frozen=True)
class Switch:
    src: int  # scalar temp id feeding the (integer) test
    modulus: int  # test = Mod(Floor(Abs(read)), modulus)
    conds: tuple  # distinct int case labels
    bodies: tuple  # one node per cond
    default: object | None  # None -> default-less (miss falls through to exit)


@dataclass(frozen=True)
class Loop:
    bound: int  # 1..8
    body: object


@dataclass(frozen=True)
class Program:
    root: object
    n_scalars: int
    array_sizes: tuple = field(default_factory=tuple)


# ==========================================================================
# Hypothesis strategies.
# ==========================================================================


@st.composite
def _leaf(draw, n_scalars, array_sizes):
    choices = ["const"]
    if n_scalars:
        choices.append("read")
    if array_sizes:
        choices.append("arr")
    kind = draw(st.sampled_from(choices))
    if kind == "read":
        return Read(draw(st.integers(0, n_scalars - 1)))
    if kind == "arr":
        arr = draw(st.integers(0, len(array_sizes) - 1))
        return ReadArr(arr, draw(st.integers(0, array_sizes[arr] - 1)))
    return Const(draw(st.sampled_from(_CONSTS)))


@st.composite
def _expr(draw, n_scalars, array_sizes, depth):
    if depth <= 0:
        return draw(_leaf(n_scalars, array_sizes))
    kind = draw(st.sampled_from(["leaf", "leaf", "unary", "binary"]))
    if kind == "leaf":
        return draw(_leaf(n_scalars, array_sizes))
    if kind == "unary":
        return Unary(draw(st.sampled_from(_UNARY_OPS)), draw(_expr(n_scalars, array_sizes, depth - 1)))
    op = draw(st.sampled_from([*_BINARY_OPS, *_GUARDED_BINARY_OPS]))
    a = draw(_expr(n_scalars, array_sizes, depth - 1))
    if op in _GUARDED_BINARY_OPS:
        b = Const(draw(st.sampled_from(_NONZERO)))
    else:
        b = draw(_expr(n_scalars, array_sizes, depth - 1))
    return Binary(op, a, b)


@st.composite
def _stmt(draw, n_scalars, array_sizes):
    def expr():
        return draw(_expr(n_scalars, array_sizes, draw(st.integers(0, 2))))

    def scalar():
        return draw(st.integers(0, n_scalars - 1))

    kinds = ["set_scalar", "set_obs", "load_obs", "log", "log"]
    if array_sizes:
        kinds += ["set_arr_const", "set_arr_dyn", "load_arr_dyn"]
    kind = draw(st.sampled_from(kinds))
    if kind == "set_scalar":
        return SetScalar(scalar(), expr())
    if kind == "set_obs":
        return SetObs(draw(st.integers(0, 1)), draw(st.integers(0, OBS_SLOTS - 1)), expr())
    if kind == "load_obs":
        return LoadObs(scalar(), draw(st.integers(0, 1)), draw(st.integers(0, OBS_SLOTS - 1)))
    if kind == "log":
        return Log(expr())
    if kind == "set_arr_const":
        arr = draw(st.integers(0, len(array_sizes) - 1))
        return SetArrConst(arr, draw(st.integers(0, array_sizes[arr] - 1)), expr())
    if kind == "set_arr_dyn":
        return SetArrDyn(draw(st.integers(0, len(array_sizes) - 1)), scalar(), expr())
    return LoadArrDyn(scalar(), draw(st.integers(0, len(array_sizes) - 1)), scalar())


@st.composite
def _stmts(draw, n_scalars, array_sizes):
    return BlockNode(tuple(draw(st.lists(_stmt(n_scalars, array_sizes), min_size=0, max_size=4))))


@st.composite
def _node(draw, n_scalars, array_sizes, depth):
    if depth <= 0:
        return draw(_stmts(n_scalars, array_sizes))
    kind = draw(st.sampled_from(["block", "block", "seq", "if", "switch", "loop"]))
    if kind == "block":
        return draw(_stmts(n_scalars, array_sizes))
    if kind == "seq":
        n = draw(st.integers(2, 3))
        return Seq(tuple(draw(_node(n_scalars, array_sizes, depth - 1)) for _ in range(n)))
    if kind == "if":
        return If(
            draw(_expr(n_scalars, array_sizes, draw(st.integers(0, 2)))),
            draw(_node(n_scalars, array_sizes, depth - 1)),
            draw(_node(n_scalars, array_sizes, depth - 1)),
        )
    if kind == "switch":
        modulus = draw(st.integers(2, 4))
        # A mix of contiguous (0..k-1) and non-contiguous case-label sets, with
        # and without a default (default-less is legal -- a miss exits).
        if draw(st.booleans()):
            k = draw(st.integers(1, modulus))
            conds = tuple(range(k))
        else:
            conds = tuple(sorted(draw(st.sets(st.integers(0, 5), min_size=1, max_size=3))))
        bodies = tuple(draw(_node(n_scalars, array_sizes, depth - 1)) for _ in conds)
        default = draw(_node(n_scalars, array_sizes, depth - 1)) if draw(st.booleans()) else None
        src = draw(st.integers(0, n_scalars - 1))
        return Switch(src, modulus, conds, bodies, default)
    return Loop(draw(st.integers(1, 8)), draw(_node(n_scalars, array_sizes, depth - 1)))


@st.composite
def programs(draw, max_depth: int = 3):
    """Draw a ``Program`` recipe: root control-flow node plus register pool sizes."""
    n_scalars = draw(st.integers(2, 5))
    array_sizes = tuple(draw(st.lists(st.integers(2, 4), min_size=0, max_size=2)))
    depth = draw(st.integers(1, max_depth))
    root = draw(_node(n_scalars, array_sizes, depth))
    return Program(root, n_scalars, array_sizes)


# ==========================================================================
# Lowering: recipe -> fresh BasicBlock CFG.
# ==========================================================================


def _sc_place(name: str) -> BlockPlace:
    return BlockPlace(TempBlock(name, 1), 0, 0)


def _clamp(expr: IRExpr) -> IRExpr:
    lo = IRPureInstr(Op.Max, [expr, IRConst(-BOUND)])
    return IRPureInstr(Op.Min, [lo, IRConst(BOUND)])


class _Builder:
    def __init__(self, program: Program):
        self.program = program
        self.arrays = tuple(TempBlock(f"arr{i}", size) for i, size in enumerate(program.array_sizes))
        self._counter_i = 0
        self.counters: list[str] = []  # active loop-counter names (innermost last)

    def scalar(self, i: int) -> BlockPlace:
        return _sc_place(f"s{i}")

    def read_scalar(self, i: int) -> IRGet:
        return IRGet(self.scalar(i))

    def fresh_counter(self) -> str:
        name = f"k{self._counter_i}"
        self._counter_i += 1
        return name

    # -- expressions --------------------------------------------------------

    def expr(self, e) -> IRExpr:
        match e:
            case Const(value):
                return IRConst(value)
            case Read(temp):
                return self.read_scalar(temp)
            case ReadArr(arr, index):
                return IRGet(BlockPlace(self.arrays[arr], index))
            case Unary(op, a):
                return IRPureInstr(op, [self.expr(a)])
            case Binary(op, a, b):
                return IRPureInstr(op, [self.expr(a), self.expr(b)])
        raise TypeError(f"bad expr {e!r}")

    def dyn_index(self, src: int, size: int) -> IRExpr:
        # An always-in-bounds non-negative integer index. Inside a loop prefer
        # ``counter % size`` (the canonical frontend pattern); otherwise derive
        # it from a scalar read via Floor(Abs(.)) so any (even undefined) value
        # is a valid integer index.
        if self.counters:
            base = IRGet(_sc_place(self.counters[-1]))
        else:
            base = IRPureInstr(Op.Floor, [IRPureInstr(Op.Abs, [self.read_scalar(src)])])
        return IRPureInstr(Op.Mod, [base, IRConst(size)])

    # -- statements ---------------------------------------------------------

    def stmt(self, s):
        match s:
            case SetScalar(dst, expr):
                return IRSet(self.scalar(dst), _clamp(self.expr(expr)))
            case SetArrConst(arr, index, expr):
                return IRSet(BlockPlace(self.arrays[arr], index), _clamp(self.expr(expr)))
            case SetArrDyn(arr, src, expr):
                idx = self.dyn_index(src, self.arrays[arr].size)
                return IRSet(BlockPlace(self.arrays[arr], idx), _clamp(self.expr(expr)))
            case LoadArrDyn(dst, arr, src):
                idx = self.dyn_index(src, self.arrays[arr].size)
                return IRSet(self.scalar(dst), _clamp(IRGet(BlockPlace(self.arrays[arr], idx))))
            case SetObs(block, index, expr):
                return IRSet(BlockPlace(OBS_BLOCKS[block], index), self.expr(expr))
            case LoadObs(dst, block, index):
                return IRSet(self.scalar(dst), _clamp(IRGet(BlockPlace(OBS_BLOCKS[block], index))))
            case Log(expr):
                return IRInstr(Op.DebugLog, [self.expr(expr)])
        raise TypeError(f"bad stmt {s!r}")

    # -- control flow -------------------------------------------------------

    def lower(self, node, cur: BasicBlock) -> BasicBlock:
        """Lower ``node`` into ``cur``; return the block where control continues."""
        match node:
            case BlockNode(stmts):
                for s in stmts:
                    cur.statements.append(self.stmt(s))
                return cur
            case Seq(nodes):
                for n in nodes:
                    cur = self.lower(n, cur)
                return cur
            case If(test, then_, else_):
                cur.test = self.expr(test)
                then_entry = BasicBlock()
                else_entry = BasicBlock()
                cur.connect_to(else_entry, 0)
                cur.connect_to(then_entry, None)
                join = BasicBlock()
                self.lower(then_, then_entry).connect_to(join, None)
                self.lower(else_, else_entry).connect_to(join, None)
                return join
            case Switch(src, modulus, conds, bodies, default):
                cur.test = IRPureInstr(
                    Op.Mod,
                    [IRPureInstr(Op.Floor, [IRPureInstr(Op.Abs, [self.read_scalar(src)])]), IRConst(modulus)],
                )
                join = BasicBlock()
                for cond, body in zip(conds, bodies, strict=True):
                    entry = BasicBlock()
                    cur.connect_to(entry, cond)
                    self.lower(body, entry).connect_to(join, None)
                if default is not None:
                    entry = BasicBlock()
                    cur.connect_to(entry, None)
                    self.lower(default, entry).connect_to(join, None)
                return join
            case Loop(bound, body):
                counter = self.fresh_counter()
                cp = _sc_place(counter)
                cur.statements.append(IRSet(cp, IRConst(0)))
                header = BasicBlock(test=IRPureInstr(Op.Less, [IRGet(cp), IRConst(bound)]))
                cur.connect_to(header, None)
                body_entry = BasicBlock()
                after = BasicBlock()
                header.connect_to(body_entry, None)  # continue
                header.connect_to(after, 0)  # exit loop
                self.counters.append(counter)
                body_exit = self.lower(body, body_entry)
                self.counters.pop()
                step = BasicBlock(statements=[IRSet(cp, IRPureInstr(Op.Add, [IRGet(cp), IRConst(1)]))])
                body_exit.connect_to(step, None)
                step.connect_to(header, None)  # back-edge
                return after
        raise TypeError(f"bad node {node!r}")


def build_cfg(program: Program) -> BasicBlock:
    """Lower a ``Program`` recipe into a fresh ``BasicBlock`` CFG.

    Every array slot is written at entry before the body runs. This makes each
    array unconditionally live and distinctly allocated, so array reads observe
    real memory rather than an undefined slot. Undefined *scalar* reads are still
    generated freely (they are handled correctly). Undefined *array* reads are
    deliberately kept out of the random corpus and pinned in a dedicated directed
    regression instead -- see ``test_random_cfg`` (never-written array aliasing).
    """
    builder = _Builder(program)
    entry = BasicBlock()
    for arr in builder.arrays:
        for k in range(arr.size):
            entry.statements.append(IRSet(BlockPlace(arr, k), IRConst(0)))
    builder.lower(program.root, entry)
    return entry


def count_blocks(cfg: BasicBlock) -> int:
    return sum(1 for _ in traverse_cfg_reverse_postorder(cfg))
