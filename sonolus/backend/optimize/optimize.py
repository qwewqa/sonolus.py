from sonolus.backend.optimize.allocate import Allocate, AllocateBasic, AllocateFast
from sonolus.backend.optimize.constant_evaluation import SparseConditionalConstantPropagation
from sonolus.backend.optimize.copy_coalesce import CopyCoalesce
from sonolus.backend.optimize.dead_code import (
    AdvancedDeadCodeElimination,
    DeadCodeElimination,
    UnreachableCodeElimination,
)
from sonolus.backend.optimize.inlining import InlineVars
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
    AllocateFast(),  # Does dead code elimination too, so no need for a separate pass
    CoalesceFlow(),
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
