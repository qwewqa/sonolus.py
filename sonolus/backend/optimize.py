from sonolus.backend.allocate import AllocateBasic
from sonolus.backend.dead_code import UnreachableCodeElimination
from sonolus.backend.flow import BasicBlock
from sonolus.backend.passes import run_passes
from sonolus.backend.simplify import CoalesceFlow

BASIC_PASSES = [
    CoalesceFlow(),
    UnreachableCodeElimination(),
    AllocateBasic(),
]


def optimize_and_allocate(cfg: BasicBlock):
    return run_passes(cfg, BASIC_PASSES)
