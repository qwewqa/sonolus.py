from sonolus.backend.optimize.allocate import Allocate, AllocateBasic
from sonolus.backend.optimize.constant_evaluation import SparseConditionalConstantPropagation
from sonolus.backend.optimize.copy_coalesce import CopyCoalesce
from sonolus.backend.optimize.dead_code import (
    AdvancedDeadCodeElimination,
    DeadCodeElimination,
    UnreachableCodeElimination,
)
from sonolus.backend.optimize.flow import BasicBlock
from sonolus.backend.optimize.inlining import InlineVars
from sonolus.backend.optimize.passes import run_passes
from sonolus.backend.optimize.simplify import CoalesceFlow, NormalizeSwitch, RewriteToSwitch
from sonolus.backend.optimize.ssa import FromSSA, ToSSA

MINIMAL_PASSES = (
    CoalesceFlow(),
    UnreachableCodeElimination(),
    AllocateBasic(),
)

FAST_PASSES = (
    CoalesceFlow(),
    UnreachableCodeElimination(),
    AdvancedDeadCodeElimination(),
    CoalesceFlow(),
    Allocate(),
)

STANDARD_PASSES = (
    CoalesceFlow(),
    UnreachableCodeElimination(),
    DeadCodeElimination(),
    ToSSA(),
    SparseConditionalConstantPropagation(),
    UnreachableCodeElimination(),
    DeadCodeElimination(),
    CoalesceFlow(),
    InlineVars(),
    DeadCodeElimination(),
    RewriteToSwitch(),
    FromSSA(),
    CoalesceFlow(),
    CopyCoalesce(),
    AdvancedDeadCodeElimination(),
    CoalesceFlow(),
    NormalizeSwitch(),
    Allocate(),
)


def optimize_and_allocate(cfg: BasicBlock):
    return run_passes(cfg, STANDARD_PASSES)
