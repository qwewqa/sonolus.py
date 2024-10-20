from dataclasses import dataclass
from functools import cached_property

from sonolus.backend.blocks import Block, PlayBlock


@dataclass
class Mode:
    name: str
    blocks: type[Block]

    @cached_property
    def callbacks(self) -> frozenset[str]:
        cbs = set()
        for block in self.blocks:
            cbs.update(block.readable)
            cbs.update(block.writable)
        return frozenset(cbs)


PlayMode = Mode("Play", PlayBlock)
