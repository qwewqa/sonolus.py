from enum import Enum
from functools import cached_property

from sonolus.backend.blocks import Block, PlayBlock, PreviewBlock, TutorialBlock, WatchBlock


class Mode(Enum):
    blocks: type[Block]

    PLAY = (PlayBlock,)
    WATCH = (WatchBlock,)
    PREVIEW = (PreviewBlock,)
    TUTORIAL = (TutorialBlock,)

    def __init__(self, blocks: type[Block]):
        self.blocks = blocks

    @cached_property
    def callbacks(self) -> frozenset[str]:
        cbs = set()
        for block in self.blocks:
            cbs.update(block.readable)
            cbs.update(block.writable)
        return frozenset(cbs)
