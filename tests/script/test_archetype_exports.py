"""Tests that writes to exported archetype fields reach the emitted engine data.

An exported field is write-only and has no readable storage, so `run_and_validate` cannot observe it and the
interpreter has no `ExportValue`. The oracle here is the emitted node tree itself: every write the callback
performs must appear as an `ExportValue` node carrying the field's flat export index and the written value.
"""

from sonolus.backend.ir import IRInstr
from sonolus.backend.mode import Mode
from sonolus.backend.node import EngineNode, FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import STANDARD_PASSES, OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_preorder
from sonolus.build.compile import callback_to_cfg
from sonolus.script.archetype import PlayArchetype, exported
from sonolus.script.array import Array
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.vec import Vec2


def exported_writes(archetype: type[PlayArchetype]) -> dict[float, float]:
    """Compile the archetype's preprocess and return its exports as {export index: exported value}.

    The assert compares ExportValue counts, so it fires only when the optimizer dropped a write; a write the
    frontend never emitted surfaces as a missing entry in the returned dict instead. No archetype here writes
    the same index twice, which the dict could not represent.
    """
    archetype._init_fields()
    project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
    mode_state = ModeContextState(Mode.PLAY, [archetype])
    cfg = callback_to_cfg(project_state, mode_state, archetype.preprocess, "preprocess", archetype)
    emitted = _emitted_exports(cfg_to_engine_node(run_passes(cfg, STANDARD_PASSES, OptimizerConfig())))
    frontend_count = _frontend_export_count(cfg)
    assert len(emitted) == frontend_count, (
        f"the frontend emitted {frontend_count} exports but {len(emitted)} survived to the node tree"
    )
    return dict(emitted)


def _frontend_export_count(cfg: BasicBlock) -> int:
    """How many ExportValue instructions the frontend put in the unoptimized CFG."""
    return sum(
        isinstance(statement, IRInstr) and statement.op is Op.ExportValue
        for block in traverse_cfg_preorder(cfg)
        for statement in block.statements
    )


def _emitted_exports(node: EngineNode) -> list[tuple[float, float]]:
    """Every ExportValue in the tree as (index, value), in traversal order, one entry per node."""
    writes = []

    def walk(n: EngineNode):
        if not isinstance(n, FunctionNode):
            return
        if n.func is Op.ExportValue:
            index, value = n.args
            writes.append((index, value))
        for arg in n.args:
            walk(arg)

    walk(node)
    return writes


def export_indexes(archetype: type[PlayArchetype]) -> dict[str, int]:
    """The archetype's exports keyed by the name they carry in level data."""
    archetype._init_fields()
    return dict(archetype._exported_keys_)


class ScalarExport(PlayArchetype):
    value: float = exported()

    def preprocess(self):
        setattr(self, "value", 1.0)  # noqa: B010


class WholeRecordExport(PlayArchetype):
    pos: Vec2 = exported()

    def preprocess(self):
        self.pos = Vec2(2.0, 3.0)


class RecordFieldExport(PlayArchetype):
    pos: Vec2 = exported()

    def preprocess(self):
        self.pos.x = 9.0


class ArrayElementExport(PlayArchetype):
    values: Array[float, 2] = exported()

    def preprocess(self):
        self.values[1] = 7.0


class MixedExports(PlayArchetype):
    scalar: float = exported()
    pos: Vec2 = exported(name="#POS")
    values: Array[float, 2] = exported()

    def preprocess(self):
        self.scalar = 10.0
        self.pos = Vec2(11.0, 12.0)
        self.values[0] = 13.0


def test_scalar_export_is_written():
    assert export_indexes(ScalarExport) == {"value": 0}
    assert exported_writes(ScalarExport) == {0: 1.0}


def test_whole_record_export_is_written():
    assert export_indexes(WholeRecordExport) == {"pos.x": 0, "pos.y": 1}
    assert exported_writes(WholeRecordExport) == {0: 2.0, 1: 3.0}


def test_record_field_export_is_written():
    assert export_indexes(RecordFieldExport) == {"pos.x": 0, "pos.y": 1}
    assert exported_writes(RecordFieldExport) == {0: 9.0}


def test_array_element_export_is_written():
    assert export_indexes(ArrayElementExport) == {"values[0]": 0, "values[1]": 1}
    assert exported_writes(ArrayElementExport) == {1: 7.0}


def test_whole_field_and_element_wise_exports_share_one_index_space():
    assert export_indexes(MixedExports) == {
        "scalar": 0,
        "#POS.x": 1,
        "#POS.y": 2,
        "values[0]": 3,
        "values[1]": 4,
    }
    assert exported_writes(MixedExports) == {0: 10.0, 1: 11.0, 2: 12.0, 3: 13.0}
