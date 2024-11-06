from sonolus.backend.allocate import AllocateBasic
from sonolus.backend.flow import BasicBlock
from sonolus.backend.simplify import CoalesceFlow


def optimize_and_allocate(cfg: BasicBlock):
    cfg = CoalesceFlow().run(cfg)
    cfg = AllocateBasic().run(cfg)
    return cfg
