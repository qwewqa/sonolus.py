"""Regression test for sonolus.backend.optimize.flow.cfg_to_text rendering."""

from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text
from sonolus.backend.place import SSAPlace


def test_cfg_to_text_tolerates_dead_phi_source():
    # A phi referencing a block unreachable from entry must render as <dead> rather than crashing
    # the phi-source sort (block_indexes.get returned None, which can't be compared against ints).
    entry = BasicBlock()
    target = BasicBlock()
    dead = BasicBlock()  # never reachable from entry -> absent from block_indexes
    entry.connect_to(target)
    dead.connect_to(target)
    target.phis = {SSAPlace("p", 2): {entry: SSAPlace("p", 0), dead: SSAPlace("p", 1)}}

    text = cfg_to_text(entry)  # must not raise

    assert "<dead>" in text
