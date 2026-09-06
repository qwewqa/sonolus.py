"""Regression coverage for batching independent LICM loop rewrites.

Builders return fresh CFGs for comparison across optimizer implementations.
Tests use the minimal pipeline as the semantic oracle.
"""

from sonolus.backend._opt import ir  # ruff: ignore[import-private-name]
from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import MINIMAL_PASSES, OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text
from sonolus.backend.place import BlockPlace, TempBlock

RU = PlayBlock.RuntimeUpdate
WBLOCK = 20

SSA_LICM_PHASES = ["cfg_cleanup", "ssa", "gvn", "dce", "licm"]
STANDARD_PHASES = ["cfg_cleanup", "ssa", "midend_standard", "lower", "packing"]


def _scalar(name: str) -> BlockPlace:
    return BlockPlace(TempBlock(name, 1), 0, 0)


def _read(name: str) -> IRGet:
    return IRGet(_scalar(name))


def _runtime(index: int) -> IRGet:
    return IRGet(BlockPlace(RU, index))


def _dynamic_runtime(index: int) -> IRGet:
    return IRGet(BlockPlace(RU, _runtime(index)))


def _product(left: int, right: int) -> IRPureInstr:
    return IRPureInstr(Op.Multiply, [_runtime(left), _runtime(right)])


def _add(left, right) -> IRPureInstr:
    return IRPureInstr(Op.Add, [left, right])


def _log(value) -> IRInstr:
    return IRInstr(Op.DebugLog, [value])


def _licm_text(builder) -> str:
    return cfg_to_text(ir.debug_run(builder(), Mode.PLAY, None, phases=SSA_LICM_PHASES))


def _sections(text: str) -> list[str]:
    sections = []
    current = []
    for line in text.splitlines():
        if line.endswith(":") and line[:-1].isdigit():
            if current:
                sections.append("\n".join(current))
            current = []
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))
    return sections


def _run(node, seed: dict[int, list[float]]) -> Interpreter:
    interpreter = Interpreter()
    for block, values in seed.items():
        interpreter.blocks[block] = list(values)
    interpreter.run(node)
    return interpreter


def _assert_semantics(builder, seed: dict[int, list[float]]) -> Interpreter:
    reference_cfg = run_passes(builder(), MINIMAL_PASSES, OptimizerConfig(mode=Mode.PLAY, callback=None))
    candidate_cfg = ir.debug_run(builder(), Mode.PLAY, None, phases=STANDARD_PHASES)
    reference = _run(cfg_to_engine_node(reference_cfg), seed)
    candidate = _run(cfg_to_engine_node(candidate_cfg), seed)

    assert candidate.log == reference.log
    blocks = sorted((set(reference.blocks) | set(candidate.blocks)) - {10000, 3000})
    for block in blocks:
        for index in range(32):
            assert candidate.get(block, index) == reference.get(block, index)
    return reference


def build_two_independent_loops() -> BasicBlock:
    """Build two consecutive, disjoint self loops with separate invariant products."""
    entry = BasicBlock(statements=[IRSet(_scalar("i"), IRConst(0)), IRSet(_scalar("a"), IRConst(0))])
    first = BasicBlock(
        statements=[
            IRSet(_scalar("a"), _add(_read("a"), _product(0, 1))),
            IRSet(_scalar("i"), _add(_read("i"), IRConst(1))),
        ],
        test=IRPureInstr(Op.Less, [_read("i"), IRConst(3)]),
    )
    between = BasicBlock(statements=[IRSet(_scalar("j"), IRConst(0)), IRSet(_scalar("b"), IRConst(0))])
    second = BasicBlock(
        statements=[
            IRSet(_scalar("b"), _add(_read("b"), _product(2, 3))),
            IRSet(_scalar("j"), _add(_read("j"), IRConst(1))),
        ],
        test=IRPureInstr(Op.Less, [_read("j"), IRConst(2)]),
    )
    exit_ = BasicBlock(statements=[_log(_read("a")), _log(_read("b"))])

    entry.connect_to(first, None)
    first.connect_to(first, None)
    first.connect_to(between, 0)
    between.connect_to(second, None)
    second.connect_to(second, None)
    second.connect_to(exit_, 0)
    return entry


def build_cross_loop_dependency() -> BasicBlock:
    """Build disjoint loops where the second hoist consumes a value defined and hoisted by the first."""
    entry = BasicBlock(statements=[IRSet(_scalar("i"), IRConst(0)), IRSet(_scalar("a"), IRConst(0))])
    first = BasicBlock(
        statements=[
            IRSet(_scalar("shared"), _product(0, 1)),
            IRSet(_scalar("a"), _add(_read("a"), _read("shared"))),
            IRSet(_scalar("i"), _add(_read("i"), IRConst(1))),
        ],
        test=IRPureInstr(Op.Less, [_read("i"), IRConst(2)]),
    )
    between = BasicBlock(statements=[IRSet(_scalar("j"), IRConst(0)), IRSet(_scalar("b"), IRConst(0))])
    second_product = IRPureInstr(Op.Multiply, [_read("shared"), _runtime(2)])
    second = BasicBlock(
        statements=[
            IRSet(_scalar("b"), _add(_read("b"), second_product)),
            IRSet(_scalar("j"), _add(_read("j"), IRConst(1))),
        ],
        test=IRPureInstr(Op.Less, [_read("j"), IRConst(3)]),
    )
    exit_ = BasicBlock(statements=[_log(_read("a")), _log(_read("b"))])

    entry.connect_to(first, None)
    first.connect_to(first, None)
    first.connect_to(between, 0)
    between.connect_to(second, None)
    second.connect_to(second, None)
    second.connect_to(exit_, 0)
    return entry


def build_nested_enabling_loops() -> BasicBlock:
    """Build nested loops needing fixpoint motion, followed by an independent profitable loop."""
    entry = BasicBlock(statements=[IRSet(_scalar("i"), IRConst(0)), IRSet(_scalar("acc"), IRConst(0))])
    outer = BasicBlock(
        statements=[
            IRSet(BlockPlace(WBLOCK, 2), _read("i")),
            IRSet(BlockPlace(WBLOCK, 3), _read("i")),
            IRSet(_scalar("j"), IRConst(0)),
        ],
        test=IRPureInstr(Op.Less, [_read("i"), IRConst(3)]),
    )
    inner_header = BasicBlock(
        statements=[
            IRSet(BlockPlace(WBLOCK, 0), _read("j")),
            IRSet(BlockPlace(WBLOCK, 1), _read("j")),
        ],
        test=IRPureInstr(Op.Less, [_read("j"), _runtime(2)]),
    )
    inner_body = BasicBlock(
        statements=[
            IRSet(_scalar("acc"), _add(_read("acc"), _product(0, 1))),
            IRSet(_scalar("j"), _add(_read("j"), IRConst(1))),
        ]
    )
    outer_latch = BasicBlock(statements=[IRSet(_scalar("i"), _add(_read("i"), IRConst(1)))])
    after_outer = BasicBlock(statements=[IRSet(_scalar("k"), IRConst(0)), IRSet(_scalar("later"), IRConst(0))])
    later_loop = BasicBlock(
        statements=[
            IRSet(_scalar("later"), _add(_read("later"), _product(3, 4))),
            IRSet(_scalar("k"), _add(_read("k"), IRConst(1))),
        ],
        test=IRPureInstr(Op.Less, [_read("k"), IRConst(2)]),
    )
    exit_ = BasicBlock(statements=[_log(_read("acc")), _log(_read("later"))])

    entry.connect_to(outer, None)
    outer.connect_to(inner_header, None)
    outer.connect_to(after_outer, 0)
    inner_header.connect_to(inner_body, None)
    inner_header.connect_to(outer_latch, 0)
    inner_body.connect_to(inner_header, None)
    outer_latch.connect_to(outer, None)
    after_outer.connect_to(later_loop, None)
    later_loop.connect_to(later_loop, None)
    later_loop.connect_to(exit_, 0)
    return entry


def build_two_multi_entry_loops() -> BasicBlock:
    """Build two disjoint loops that each need a new phi-carrying preheader."""
    entry = BasicBlock(test=IRGet(BlockPlace(WBLOCK, 9)))
    first_a = BasicBlock(statements=[IRSet(_scalar("i"), IRConst(0)), IRSet(_scalar("a"), IRConst(10))])
    # The alternate entry deliberately leaves a undefined. Its live UNDEF operand must survive preheader splitting.
    first_b = BasicBlock(statements=[IRSet(_scalar("i"), IRConst(0))])
    first = BasicBlock(
        statements=[
            IRSet(_scalar("a"), _add(_read("a"), _product(0, 1))),
            IRSet(_scalar("i"), _add(_read("i"), IRConst(1))),
        ],
        test=IRPureInstr(Op.Less, [_read("i"), IRConst(3)]),
    )
    split_second = BasicBlock(test=IRGet(BlockPlace(WBLOCK, 10)))
    second_a = BasicBlock(statements=[IRSet(_scalar("j"), IRConst(0)), IRSet(_scalar("b"), IRConst(100))])
    second_b = BasicBlock(statements=[IRSet(_scalar("j"), IRConst(0)), IRSet(_scalar("b"), IRConst(200))])
    second = BasicBlock(
        statements=[
            IRSet(_scalar("b"), _add(_read("b"), _product(2, 3))),
            IRSet(_scalar("j"), _add(_read("j"), IRConst(1))),
        ],
        test=IRPureInstr(Op.Less, [_read("j"), IRConst(4)]),
    )
    exit_ = BasicBlock(statements=[_log(_read("a")), _log(_read("b"))])

    entry.connect_to(first_a, 0)
    entry.connect_to(first_b, None)
    first_a.connect_to(first, None)
    first_b.connect_to(first, None)
    first.connect_to(first, None)
    first.connect_to(split_second, 0)
    split_second.connect_to(second_a, 0)
    split_second.connect_to(second_b, None)
    second_a.connect_to(second, None)
    second_b.connect_to(second, None)
    second.connect_to(second, None)
    second.connect_to(exit_, 0)
    return entry


def build_mixed_preheaders_with_dynamic_get() -> BasicBlock:
    """Build independent reused- and new-preheader hoists, including a dynamic readonly load."""
    entry = BasicBlock(statements=[IRSet(_scalar("i"), IRConst(0)), IRSet(_scalar("a"), IRConst(0))])
    first = BasicBlock(
        statements=[
            IRSet(_scalar("sample"), _dynamic_runtime(0)),
            IRSet(_scalar("a"), _add(_read("a"), _read("sample"))),
            IRSet(_scalar("i"), _add(_read("i"), IRConst(1))),
        ],
        test=IRPureInstr(Op.Less, [_read("i"), IRConst(2)]),
    )
    split_second = BasicBlock(test=IRGet(BlockPlace(WBLOCK, 9)))
    second_a = BasicBlock(statements=[IRSet(_scalar("j"), IRConst(0)), IRSet(_scalar("b"), IRConst(100))])
    second_b = BasicBlock(statements=[IRSet(_scalar("j"), IRConst(0)), IRSet(_scalar("b"), IRConst(200))])
    independent_product = _product(2, 3)
    second = BasicBlock(
        statements=[
            IRSet(_scalar("b"), _add(_read("b"), independent_product)),
            IRSet(_scalar("j"), _add(_read("j"), IRConst(1))),
        ],
        test=IRPureInstr(Op.Less, [_read("j"), IRConst(3)]),
    )
    exit_ = BasicBlock(statements=[_log(_read("a")), _log(_read("b"))])

    entry.connect_to(first, None)
    first.connect_to(first, None)
    first.connect_to(split_second, 0)
    split_second.connect_to(second_a, 0)
    split_second.connect_to(second_b, None)
    second_a.connect_to(second, None)
    second_b.connect_to(second, None)
    second.connect_to(second, None)
    second.connect_to(exit_, 0)
    return entry


CASE_BUILDERS = {
    "independent": build_two_independent_loops,
    "cross_loop_dependency": build_cross_loop_dependency,
    "mixed_preheaders_dynamic_get": build_mixed_preheaders_with_dynamic_get,
    "nested_enabling": build_nested_enabling_loops,
    "multi_entry_undef": build_two_multi_entry_loops,
}


def test_two_independent_loops_hoist_both_products_without_crossing_headers():
    text = _licm_text(build_two_independent_loops)
    assert text.count(" * ") == 2
    assert all(" * " not in section for section in _sections(text) if "phi(" in section)

    reference = _assert_semantics(
        build_two_independent_loops,
        {RU.value: [2.0, 3.0, 4.0, 5.0]},
    )
    assert reference.log == [18.0, 40.0]


def test_disjoint_loops_preserve_dependency_on_value_hoisted_from_earlier_loop():
    text = _licm_text(build_cross_loop_dependency)
    assert text.count(" * ") == 2
    assert all(" * " not in section for section in _sections(text) if "phi(" in section)

    reference = _assert_semantics(
        build_cross_loop_dependency,
        {RU.value: [2.0, 3.0, 5.0]},
    )
    assert reference.log == [12.0, 90.0]


def test_nested_inner_hoist_enables_motion_past_the_outer_loop():
    text = _licm_text(build_nested_enabling_loops)
    sections = _sections(text)
    nested_product = next(
        section
        for section in sections
        if " * " in section and "RuntimeUpdate[0]" in section and "RuntimeUpdate[1]" in section
    )
    later_product = next(
        section
        for section in sections
        if " * " in section and "RuntimeUpdate[3]" in section and "RuntimeUpdate[4]" in section
    )
    outer_headers = [section for section in sections if "20[2]" in section and "< 3" in section]
    inner_headers = [section for section in sections if "20[0]" in section and "RuntimeUpdate[2]" in section]

    assert text.count(" * ") == 2
    assert len(outer_headers) == len(inner_headers) == 1
    assert outer_headers[0] != inner_headers[0]
    assert nested_product == sections[0]
    assert nested_product not in outer_headers
    assert nested_product not in inner_headers
    assert "phi(" not in later_product

    zero_trip = _assert_semantics(
        build_nested_enabling_loops,
        {RU.value: [2.0, 3.0, 0.0, 4.0, 5.0], WBLOCK: [0.0] * 8},
    )
    entered = _assert_semantics(
        build_nested_enabling_loops,
        {RU.value: [2.0, 3.0, 2.0, 4.0, 5.0], WBLOCK: [0.0] * 8},
    )
    assert zero_trip.log == [0.0, 40.0]
    assert entered.log == [36.0, 40.0]


def test_two_multi_entry_loops_preserve_split_phis_and_live_undef():
    text = _licm_text(build_two_multi_entry_loops)
    sections = _sections(text)
    product_sections = [section for section in sections if " * " in section]
    loop_headers = [section for section in sections if "< 3" in section or "< 4" in section]

    assert len(product_sections) == 2
    assert all("phi(" in section for section in product_sections)
    assert all(" * " not in section for section in loop_headers)
    assert sum("undef" in section for section in product_sections) == 1

    first_a_second_b = [0.0] * 16
    first_a_second_b[10] = 1.0
    reference = _assert_semantics(
        build_two_multi_entry_loops,
        {RU.value: [2.0, 3.0, 4.0, 5.0], WBLOCK: first_a_second_b},
    )
    assert reference.log == [28.0, 280.0]

    # Taking the first loop's undefined entry exercises the live UNDEF operand after preheader splitting.
    first_b_second_a = [0.0] * 16
    first_b_second_a[9] = 1.0
    reference = _assert_semantics(
        build_two_multi_entry_loops,
        {RU.value: [2.0, 3.0, 4.0, 5.0], WBLOCK: first_b_second_a},
    )
    assert len(reference.log) == 2
    assert reference.log[1] == 180.0


def test_mixed_new_and_reused_preheaders_preserve_dynamic_get_index():
    text = _licm_text(build_mixed_preheaders_with_dynamic_get)
    sections = _sections(text)
    dynamic_get = [section for section in sections if "RuntimeUpdate[v." in section]
    products = [section for section in sections if " * " in section]
    loop_headers = [section for section in sections if "< 2" in section or "< 3" in section]

    assert dynamic_get == [sections[0]]
    assert "phi(" not in dynamic_get[0]
    assert len(products) == 1
    assert "phi(" in products[0]
    assert all("RuntimeUpdate[v." not in section and " * " not in section for section in loop_headers)

    first_a = [0.0] * 16
    reference = _assert_semantics(
        build_mixed_preheaders_with_dynamic_get,
        {RU.value: [2.0, 0.0, 7.0, 4.0], WBLOCK: first_a},
    )
    assert reference.log == [14.0, 184.0]

    first_b = [0.0] * 16
    first_b[9] = 1.0
    reference = _assert_semantics(
        build_mixed_preheaders_with_dynamic_get,
        {RU.value: [2.0, 0.0, 7.0, 4.0], WBLOCK: first_b},
    )
    assert reference.log == [14.0, 284.0]
