"""The checked-in generated op tables stay in sync with ops.py.

Guards both the source-file drift (regenerate-and-compare) and the *compiled*
static C table exposed by the extension. See tools/gen_ops.py.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sonolus.backend._opt import (
    ir,  # noqa: PLC2701
    kernels,  # noqa: PLC2701
)
from sonolus.backend.ops import Op

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The 42 ops SCCP folds, six more (Sign/Trunc/Unlerp/UnlerpClamped/Judge/JudgeSimple),
# the five strict-select ops, and the 36 Ease* ops -- kept here as an independent
# copy so the test cross-checks the generator rather than merely re-running it.
_EXPECTED_SCCP_42 = {
    "Equal", "NotEqual", "Greater", "GreaterOr", "Less", "LessOr", "Not", "And", "Or",
    "Negate", "Add", "Subtract", "Multiply", "Divide", "Power", "Log", "Ceil", "Floor",
    "Round", "Frac", "Mod", "Rem", "Sin", "Cos", "Tan", "Sinh", "Cosh", "Tanh",
    "Arcsin", "Arccos", "Arctan", "Arctan2", "Max", "Min", "Abs", "Clamp", "Degree",
    "Radian", "Lerp", "LerpClamped", "Remap", "RemapClamped",
}  # fmt: skip
_EXPECTED_ADDITIONS = {"Sign", "Trunc", "Unlerp", "UnlerpClamped", "Judge", "JudgeSimple"}
# control_flow=True yet foldable strict value selects (like And/Or).
_EXPECTED_SELECT = {"If", "Switch", "SwitchWithDefault", "SwitchInteger", "SwitchIntegerWithDefault"}
_EXPECTED_EASE = {op.value for op in Op if op.value.startswith("Ease")}
_EXPECTED_FOLDABLE = _EXPECTED_SCCP_42 | _EXPECTED_ADDITIONS | _EXPECTED_SELECT | _EXPECTED_EASE


def _load_gen_ops():
    spec = importlib.util.spec_from_file_location("gen_ops", _REPO_ROOT / "tools" / "gen_ops.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_files_in_sync():
    gen_ops = _load_gen_ops()
    for path, expected in gen_ops.generate().items():
        assert path.exists(), f"{path} is missing; run `python tools/gen_ops.py`"
        actual = path.read_text(encoding="utf-8")
        assert actual == expected, f"{path.name} is out of date with ops.py; run `python tools/gen_ops.py`"


def test_compiled_table_matches_ops():
    ops = list(Op)
    assert ir.op_runtime_count() == len(ops)
    assert ir.op_table_size() == len(ops) + 5  # + PHI/CONST/GET/SET/UNDEF
    for i, op in enumerate(ops):
        name, pure, side_effects, control_flow = ir.op_table_entry(i)
        assert name == op.value
        assert bool(pure) == op.pure
        assert bool(side_effects) == op.side_effects
        assert bool(control_flow) == op.control_flow


def test_synthetic_opcodes_present():
    base = ir.op_runtime_count()
    names = [ir.op_table_entry(base + k)[0] for k in range(5)]
    assert names == ["PHI", "CONST", "GET", "SET", "UNDEF"]


# --------------------------------------------------------------------------
# Foldability table: the exactly-89 pure, data-independent ops the C fold
# kernels evaluate.
# --------------------------------------------------------------------------


def test_generator_foldable_list_is_exactly_89():
    gen_ops = _load_gen_ops()
    names = gen_ops.foldable_op_names()
    assert gen_ops.FOLDABLE_COUNT == 89
    assert len(names) == 89
    assert len(set(names)) == 89
    assert set(names) == _EXPECTED_FOLDABLE
    assert len(_EXPECTED_SCCP_42) == 42
    assert len(_EXPECTED_SELECT) == 5
    assert len(_EXPECTED_EASE) == 36
    # Declaration order (id-aligned) so the generated static array lines up.
    all_names = [op.value for op in Op]
    assert names == [n for n in all_names if n in _EXPECTED_FOLDABLE]


def test_foldable_excludes_env_dependent_and_control_flow():
    # Pure but must NOT fold: environment-dependent + structural control-flow. The
    # five select ops are NO LONGER excluded (see test_select_ops_are_foldable_pure).
    for name in (
        "BeatToBPM", "BeatToTime", "TimeToScaledTime", "TimeToTimeScale",
        "HasEffectClip", "HasParticleEffect", "HasSkinSprite",
        "StreamGetValue", "StreamHas", "StreamGetNextKey",
        "Execute", "Execute0", "While", "DoWhile", "JumpLoop", "Block", "Break",
    ):  # fmt: skip
        assert name not in _EXPECTED_FOLDABLE
        assert not kernels.is_op_foldable(_op_id(name))
    # And/Or are control_flow=True yet ARE foldable (short-circuit value folds).
    assert Op.And.control_flow
    assert Op.Or.control_flow
    assert kernels.is_op_foldable(_op_id("And"))
    assert kernels.is_op_foldable(_op_id("Or"))


def test_select_ops_are_foldable_pure():
    # The five strict-select value ops: control_flow=True yet foldable + pure.
    for name in _EXPECTED_SELECT:
        assert name in _EXPECTED_FOLDABLE
        assert Op(name).control_flow
        assert Op(name).pure
        assert kernels.is_op_foldable(_op_id(name))


def test_compiled_foldable_table_matches_generator():
    gen_ops = _load_gen_ops()
    foldable_names = set(gen_ops.foldable_op_names())
    for i, op in enumerate(Op):
        expected = op.value in foldable_names
        assert kernels.is_op_foldable(i) == expected, op.value
        if expected:
            # Every foldable op is pure (never side-effecting).
            assert op.pure, op.value
    # The compiled id list maps back to exactly the 89 foldable op names.
    got_names = {list(Op)[i].value for i in kernels.foldable_op_ids()}
    assert got_names == _EXPECTED_FOLDABLE


def test_synthetic_opcodes_not_foldable():
    base = ir.op_runtime_count()
    for k in range(5):  # PHI/CONST/GET/SET/UNDEF
        assert not kernels.is_op_foldable(base + k)


def _op_id(name: str) -> int:
    return {op.value: i for i, op in enumerate(Op)}[name]
