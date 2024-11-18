from sonolus.backend.allocate import AllocateBasic
from sonolus.backend.constant_evaluation import SparseConditionalConstantPropagation
from sonolus.backend.dead_code import DeadCodeElimination, UnreachableCodeElimination
from sonolus.backend.flow import BasicBlock
from sonolus.backend.inlining import InlineSingleUseVars
from sonolus.backend.passes import run_passes
from sonolus.backend.simplify import CoalesceFlow
from sonolus.backend.ssa import FromSSA, ToSSA

MINIMAL_PASSES = [
    CoalesceFlow(),
    UnreachableCodeElimination(),
    AllocateBasic(),
]

STANDARD_PASSES = [
    CoalesceFlow(),
    UnreachableCodeElimination(),
    DeadCodeElimination(),
    ToSSA(),
    SparseConditionalConstantPropagation(),
    UnreachableCodeElimination(),
    DeadCodeElimination(),
    CoalesceFlow(),
    InlineSingleUseVars(),
    FromSSA(),
    DeadCodeElimination(),
    CoalesceFlow(),
    AllocateBasic(),
]


def optimize_and_allocate(cfg: BasicBlock):
    return run_passes(cfg, STANDARD_PASSES)
