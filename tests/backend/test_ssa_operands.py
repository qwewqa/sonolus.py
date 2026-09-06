"""SSA operand ordering, wide phis, and dynamic place round-trips."""

import pytest

from sonolus.backend._opt import ir  # ruff: ignore[import-private-name]
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_reverse_postorder
from sonolus.backend.place import BlockPlace
from tests.backend.test_ssa import _assert_semantics_preserved, _sc, _ssa_text


def _ordered_execute(width: int, base: int = 1000) -> IRPureInstr:
    args = []
    for i in range(width):
        value = base + i
        if i % 2 == 0:
            args.append(IRPureInstr(Op.Subtract, [IRConst(value + 37), IRConst(37)]))
        else:
            args.append(IRPureInstr(Op.Execute, [IRConst(-value), IRConst(value)]))
    return IRPureInstr(Op.Execute, args)


@pytest.mark.parametrize("width", [0, 1, 65])
def test_nested_operand_order_survives_ssa_vector_growth(width):
    def build():
        return BasicBlock(statements=[IRSet(BlockPlace(500, 0), _ordered_execute(width))])

    ssa = ir.debug_run(build(), phases=["ssa"])
    definitions = [stmt for stmt in ssa.statements if isinstance(stmt, IRSet) and isinstance(stmt.value, IRPureInstr)]
    assert len(definitions) == width + 1
    assert [stmt.value for stmt in definitions[:-1]] == _ordered_execute(width).args
    assert definitions[-1].value == IRPureInstr(Op.Execute, [IRGet(stmt.place) for stmt in definitions[:-1]])
    assert ssa.statements[-1] == IRSet(BlockPlace(500, 0), IRGet(definitions[-1].place))

    original, roundtrip = _assert_semantics_preserved(build)
    expected = 0 if width == 0 else 1000 + width - 1
    assert original.blocks[500][0] == expected
    assert roundtrip.blocks[500][0] == expected


def _many_predecessor_phi() -> BasicBlock:
    entry = BasicBlock(test=IRGet(BlockPlace(500, 0)))
    join = BasicBlock(
        statements=[
            IRInstr(Op.DebugLog, [IRGet(_sc("x"))]),
            IRInstr(Op.DebugPause, [IRGet(_sc("x"))]),
        ]
    )
    for i in range(65):
        predecessor = BasicBlock(statements=[IRSet(_sc("x"), IRConst(10_000 + i))])
        entry.connect_to(predecessor, i)
        predecessor.connect_to(join)
    return entry


def test_wide_phi_operands_follow_incoming_edge_order():
    ssa = ir.debug_run(_many_predecessor_phi(), phases=["ssa"])
    phi_blocks = [block for block in traverse_cfg_reverse_postorder(ssa) if block.phis]

    assert len(phi_blocks) == 1
    phi_sources = next(iter(phi_blocks[0].phis.values()))
    assert len(phi_sources) == 65
    for predecessor, operand in phi_sources.items():
        (incoming_edge,) = predecessor.incoming
        assert operand == IRConst(10_000 + incoming_edge.cond)


def _loop_with_operand_growth() -> BasicBlock:
    entry, head, body, exit_block = (BasicBlock() for _ in range(4))
    entry.statements = [
        IRSet(_sc("invariant"), IRConst(42)),
        IRSet(_sc("counter"), IRConst(0)),
    ]
    entry.connect_to(head)
    head.statements = [
        IRInstr(Op.DebugLog, [IRGet(_sc("invariant"))]),
        IRSet(BlockPlace(510, 0), _ordered_execute(65, 2000)),
    ]
    head.test = IRPureInstr(Op.Less, [IRGet(_sc("counter")), IRConst(3)])
    head.connect_to(exit_block, 0)
    head.connect_to(body)
    body.statements = [
        IRSet(BlockPlace(510, 1), _ordered_execute(65, 3000)),
        IRSet(_sc("counter"), IRPureInstr(Op.Add, [IRGet(_sc("counter")), IRConst(1)])),
    ]
    body.connect_to(head)
    exit_block.statements = [IRInstr(Op.DebugPause, [IRGet(_sc("counter"))])]
    return entry


def test_retained_and_trivial_phis_survive_operand_growth():
    text = _ssa_text(_loop_with_operand_growth())

    # The invariant's deferred phi is removed; only the loop-carried counter phi remains.
    assert text.count("phi(") == 1
    original, roundtrip = _assert_semantics_preserved(_loop_with_operand_growth)
    assert original.log == [42, 42, 42, 42]
    assert roundtrip.log == original.log
    assert original.blocks[510][:2] == [2064, 3064]
    assert roundtrip.blocks[510][:2] == original.blocks[510][:2]


def _dynamic_place() -> BlockPlace:
    block = IRPureInstr(Op.Execute, [IRConst(-700), IRGet(BlockPlace(500, 0))])
    index = IRPureInstr(Op.Execute, [IRConst(-3), IRGet(BlockPlace(500, 1))])
    return BlockPlace(block, index, 2)


def _dynamic_get_set() -> BasicBlock:
    value = IRPureInstr(
        Op.Execute,
        [
            IRConst(-1),
            IRPureInstr(Op.Subtract, [IRGet(BlockPlace(500, 2)), IRConst(0)]),
        ],
    )
    return BasicBlock(
        statements=[
            IRSet(BlockPlace(500, 0), IRConst(700)),
            IRSet(BlockPlace(500, 1), IRConst(3)),
            IRSet(BlockPlace(500, 2), IRConst(1234)),
            IRSet(_dynamic_place(), value),
            IRSet(BlockPlace(501, 0), IRGet(_dynamic_place())),
        ]
    )


def test_dynamic_block_index_get_set_preserves_operand_roles():
    original, roundtrip = _assert_semantics_preserved(_dynamic_get_set)

    assert original.blocks[700][5] == 1234
    assert original.blocks[501][0] == 1234
    assert roundtrip.blocks[700][5] == 1234
    assert roundtrip.blocks[501][0] == 1234
