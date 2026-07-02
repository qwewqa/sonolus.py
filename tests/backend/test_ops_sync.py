"""The checked-in generated op tables stay in sync with ops.py.

Guards both the source-file drift (regenerate-and-compare) and the *compiled*
static C table exposed by the extension. See tools/gen_ops.py and
OPTIMIZER_REWRITE.md section 6.1.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sonolus.backend._opt import ir  # noqa: PLC2701
from sonolus.backend.ops import Op

_REPO_ROOT = Path(__file__).resolve().parents[2]


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
        assert actual == expected, (
            f"{path.name} is out of date with ops.py; run `python tools/gen_ops.py`"
        )


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
