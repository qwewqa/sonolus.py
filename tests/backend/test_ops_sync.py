"""Checks that the generated Rust `Op` enum is in sync with `sonolus/backend/ops.py`."""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = REPO_ROOT / "tools" / "gen_ops.py"
GENERATED_PATH = REPO_ROOT / "rust" / "sonolus-backend-core" / "src" / "ops.rs"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_ops", GENERATOR_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_ops_rs_is_in_sync():
    expected = _load_generator().generate().encode("utf-8")
    assert GENERATED_PATH.exists(), f"{GENERATED_PATH} is missing; run `uv run python tools/gen_ops.py`"
    actual = GENERATED_PATH.read_bytes()
    if actual != expected:
        pytest.fail(
            f"{GENERATED_PATH.relative_to(REPO_ROOT)} is out of sync with sonolus/backend/ops.py; "
            f"run `uv run python tools/gen_ops.py` to regenerate it "
            f"(checked-in file: {len(actual)} bytes, expected: {len(expected)} bytes)"
        )


def test_generator_is_deterministic():
    gen = _load_generator()
    assert gen.generate() == gen.generate()


def test_generated_file_mentions_generator():
    head = GENERATED_PATH.read_text(encoding="utf-8").splitlines()[:12]
    assert any("GENERATED FILE" in line and "DO NOT EDIT" in line for line in head)
    assert any("tools/gen_ops.py" in line for line in head)


def test_generated_file_covers_all_ops():
    from sonolus.backend.ops import Op

    content = GENERATED_PATH.read_text(encoding="utf-8")
    assert f"pub const COUNT: u16 = {len(list(Op))};" in content
    for i, op in enumerate(Op):
        assert f"    {op.name} = {i},\n" in content
