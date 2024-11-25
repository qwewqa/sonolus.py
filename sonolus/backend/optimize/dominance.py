from sonolus.backend.optimize.flow import (
    BasicBlock,
    traverse_cfg_reverse_postorder,
)
from sonolus.backend.optimize.passes import CompilerPass


class DominanceFrontiers(CompilerPass):
    def destroys(self) -> set[CompilerPass] | None:
        return set()

    def run(self, entry: BasicBlock) -> BasicBlock:
        blocks = list(traverse_cfg_reverse_postorder(entry))

        self.number_blocks(blocks)
        self.initialize_idoms(blocks, entry)
        self.compute_idoms(blocks)
        self.build_dominator_tree(blocks)
        self.compute_dominance_frontiers(blocks)

        return entry

    def number_blocks(self, blocks: list[BasicBlock]):
        """Assign a unique number to each block based on reverse post-order."""
        for idx, block in enumerate(blocks):
            block.num = idx

    def initialize_idoms(self, blocks: list[BasicBlock], entry_block: BasicBlock):
        """Initialize immediate dominators for each block."""
        for block in blocks:
            block.idom = None
        entry_block.idom = entry_block

    def compute_idoms(self, blocks: list[BasicBlock]):
        """Iteratively compute the immediate dominators of each block."""
        changed = True
        while changed:
            changed = False
            for b in blocks[1:]:  # Skip the entry block
                new_idom = None
                for edge in b.incoming:
                    p = edge.src
                    if p.idom is not None:
                        if new_idom is None:
                            new_idom = p
                        else:
                            new_idom = self.intersect(p, new_idom)
                if b.idom != new_idom:
                    b.idom = new_idom
                    changed = True

    def build_dominator_tree(self, blocks: list[BasicBlock]):
        """Construct the dominator tree using the immediate dominators."""
        for block in blocks:
            block.dom_children = []

        for block in blocks:
            if block.idom != block:
                block.idom.dom_children.append(block)

    def compute_dominance_frontiers(self, blocks: list[BasicBlock]):
        """Compute the dominance frontiers for all blocks."""
        for block in blocks:
            block.df = set()

        for b in blocks:
            if len(b.incoming) >= 2:
                for edge in b.incoming:
                    p = edge.src
                    runner = p
                    while runner != b.idom:
                        runner.df.add(b)
                        runner = runner.idom

    def intersect(self, b1: BasicBlock, b2: BasicBlock) -> BasicBlock:
        """Helper function to find the closest common dominator of two blocks."""
        while b1 != b2:
            while b1.num > b2.num:
                b1 = b1.idom
            while b2.num > b1.num:
                b2 = b2.idom
        return b1

    def __eq__(self, other):
        return isinstance(other, DominanceFrontiers)

    def __hash__(self):
        return hash(DominanceFrontiers)


def get_df(block: BasicBlock) -> set[BasicBlock]:
    return block.df


def get_dom_children(block: BasicBlock) -> list[BasicBlock]:
    return block.dom_children
