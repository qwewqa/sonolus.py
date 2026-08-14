"""Regression tests for flow-graph utilities."""

from sonolus.backend.ir import IRConst, IRInstr
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock, FlowEdge, cfg_to_text
from sonolus.backend.place import SSAPlace


def test_basic_block_retains_empty_supplied_containers():
    phis = {}
    statements = []
    incoming = set()
    outgoing = set()
    block = BasicBlock(phi=phis, statements=statements, incoming=incoming, outgoing=outgoing)

    assert block.phis is phis
    assert block.statements is statements
    assert block.incoming is incoming
    assert block.outgoing is outgoing

    phi = SSAPlace("p", 0)
    statement = IRInstr(Op.DebugLog, [IRConst(1)])
    incoming_edge = FlowEdge(BasicBlock(), block)
    outgoing_edge = FlowEdge(block, BasicBlock())
    phis[phi] = {}
    statements.append(statement)
    incoming.add(incoming_edge)
    outgoing.add(outgoing_edge)

    assert block.phis[phi] == {}
    assert block.statements == [statement]
    assert block.incoming == {incoming_edge}
    assert block.outgoing == {outgoing_edge}


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
