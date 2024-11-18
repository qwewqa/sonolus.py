from sonolus.backend.allocate import Allocate, AllocateBasic
from sonolus.backend.coalesce import CopyCoalesce
from sonolus.backend.constant_evaluation import SparseConditionalConstantPropagation
from sonolus.backend.dead_code import DeadCodeElimination, UnreachableCodeElimination
from sonolus.backend.flow import BasicBlock
from sonolus.backend.inlining import InlineVars
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
    InlineVars(),
    FromSSA(),
    CoalesceFlow(),
    CopyCoalesce(),
    DeadCodeElimination(),
    CoalesceFlow(),
    Allocate(),
]


def optimize_and_allocate(cfg: BasicBlock):
    return run_passes(cfg, STANDARD_PASSES)
