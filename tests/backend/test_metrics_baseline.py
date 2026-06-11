"""Baseline-file integrity checks for the T2.4 metrics ratchet (PORT.md T2.4, G3.3).

The files under ``rust/baselines/`` are the ratchet contract: the G3.x gates compare
the Rust backend against them, so accidental deletion or corruption must fail CI. These
tests assert existence, schema version, row counts, and internal consistency only —
they never re-run timings or dynamic measurements (those live in
``tools/metrics.py baseline``).

Additionally, the pure-Python seeded memory fill in ``tools/metrics.py`` is pinned
bit-identical to the Rust differential harness's fill
(``sonolus-backend-core::diff::build_memory``, exposed as
``sonolus_backend.seeded_memory``), so the documented fill algorithm stays one
algorithm.
"""

import importlib.util
import json
import math
import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_DIR = REPO_ROOT / "rust" / "baselines"
PYTHON_STANDARD_FILE = BASELINE_DIR / "python-standard.json"
PYTHON_FAST_FILE = BASELINE_DIR / "python-fast.json"
RUST_CORPUS_FILE = BASELINE_DIR / "rust-corpus.json"
TESTDATA_DIR = REPO_ROOT / "rust" / "testdata"

SCHEMA_VERSION = 1
MIN_CALLBACKS = 250


def _load(path: Path) -> dict:
    assert path.exists(), f"missing baseline file: {path} (regenerate with `uv run python tools/metrics.py`)"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _load_metrics_module():
    """Loads tools/metrics.py under a private name (tools/ is not a package)."""
    spec = importlib.util.spec_from_file_location("_t24_metrics", REPO_ROOT / "tools" / "metrics.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_python_standard_baseline_schema():
    doc = _load(PYTHON_STANDARD_FILE)
    assert doc["schema"] == SCHEMA_VERSION
    assert doc["kind"] == "python-standard"
    assert doc["level"] == "standard"
    assert doc["timing_runs"] >= 5
    rows = doc["rows"]
    assert doc["callback_count"] == len(rows)
    assert len(rows) >= MIN_CALLBACKS

    labels = [r["label"] for r in rows]
    assert labels == sorted(labels), "rows must be sorted by label"
    assert len(set(labels)) == len(labels), "labels must be unique"

    for row in rows:
        assert row.keys() >= {"label", "mode", "callback", "static_nodes", "dag_size", "dynamic", "wall_time_ms"}
        assert isinstance(row["static_nodes"], int)
        assert row["static_nodes"] > 0
        assert isinstance(row["dag_size"], int)
        assert row["dag_size"] > 0
        assert isinstance(row["wall_time_ms"], int | float)
        assert row["wall_time_ms"] >= 0
        dynamic = row["dynamic"]
        if dynamic is None:
            assert row["dynamic_null_reason"], "dynamic-null rows must record a reason"
            continue
        assert dynamic.keys() >= {"seed_index", "eval_count", "dispatch_count", "log_len", "outcome"}
        assert dynamic["seed_index"] in range(len(doc["seed_attempts"]))
        assert isinstance(dynamic["eval_count"], int)
        assert dynamic["eval_count"] > 0
        assert isinstance(dynamic["dispatch_count"], int)
        assert dynamic["outcome"]["status"] in {"ok", "error"}

    # Aggregates must equal the row sums (cheap recompute; no timings re-run).
    dynamic_rows = [r for r in rows if r["dynamic"] is not None]
    agg = doc["aggregates"]
    assert agg["static_nodes"] == sum(r["static_nodes"] for r in rows)
    assert agg["dag_size"] == sum(r["dag_size"] for r in rows)
    assert agg["eval_count"] == sum(r["dynamic"]["eval_count"] for r in dynamic_rows)
    assert agg["dispatch_count"] == sum(r["dynamic"]["dispatch_count"] for r in dynamic_rows)
    assert agg["dynamic_rows"] == len(dynamic_rows)
    assert agg["dynamic_null"] == [r["label"] for r in rows if r["dynamic"] is None]


def test_python_fast_baseline_schema():
    doc = _load(PYTHON_FAST_FILE)
    assert doc["schema"] == SCHEMA_VERSION
    assert doc["kind"] == "python-fast"
    assert doc["level"] == "fast"
    assert doc["timing_runs"] >= 5
    rows = doc["rows"]
    assert doc["callback_count"] == len(rows)
    for row in rows:
        assert row.keys() >= {"label", "wall_time_ms"}
        assert isinstance(row["wall_time_ms"], int | float)
        assert row["wall_time_ms"] >= 0
    # The fast file covers exactly the same callbacks as the standard file.
    standard = _load(PYTHON_STANDARD_FILE)
    assert [r["label"] for r in rows] == [r["label"] for r in standard["rows"]]


def test_rust_corpus_baseline_schema():
    doc = _load(RUST_CORPUS_FILE)
    assert doc["schema"] == SCHEMA_VERSION
    assert doc["kind"] == "rust-corpus"
    rows = doc["rows"]
    assert doc["entry_count"] == len(rows)

    manifest = json.loads((TESTDATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    vector_entries = {e["hash"]: e["vectors"] for e in manifest["entries"] if e["vectors"] > 0}
    assert {r["cfg"] for r in rows} == set(vector_entries), "corpus rows must cover every vector-bearing entry"
    for row in rows:
        assert len(row["vectors"]) == vector_entries[row["cfg"]]
        assert row["static_nodes"] > 0
        assert row["dag_size"] > 0
        for vec in row["vectors"]:
            assert vec["eval_count"] > 0

    agg = doc["aggregates"]
    assert agg["static_nodes"] == sum(r["static_nodes"] for r in rows)
    assert agg["eval_count"] == sum(v["eval_count"] for r in rows for v in r["vectors"])
    assert agg["vector_count"] == sum(len(r["vectors"]) for r in rows)


def test_seeded_fill_matches_rust_build_memory():
    """The pure-Python fill (tools/metrics.py) is bit-identical to diff.rs build_memory."""
    sonolus_backend = pytest.importorskip("sonolus_backend")
    from sonolus.backend.encode import encode_cfg
    from tests.backend.test_cfg_roundtrip import REAL_CALLBACKS
    from tests.backend.test_emit_ab import trace_cfg_with_rom

    metrics = _load_metrics_module()
    for callback in REAL_CALLBACKS[:4]:
        cfg, _rom = trace_cfg_with_rom(callback)
        blocks = metrics.discover_read_blocks(cfg)
        data = encode_cfg(cfg)
        for seed in (0, 1, 0x5EED_0001, 0xFFFF_FFFF_FFFF_FFFF):
            python_fill = metrics.build_fill(blocks, seed)
            rust_fill = sonolus_backend.seeded_memory(data, seed)
            assert [b for b, _ in python_fill] == [b for b, _ in rust_fill], "discovered blocks differ"
            for (block, py_values), (_, rust_values) in zip(python_fill, rust_fill, strict=True):
                py_bits = [struct.pack("<d", v) for v in py_values]
                rust_bits = [struct.pack("<d", v) for v in rust_values]
                assert py_bits == rust_bits, f"fill differs for block {block} seed {seed}"


def test_legacy_node_converter_pins():
    """The legacy-tree converter agrees with the Rust counters on a hand-built tree."""
    sonolus_backend = pytest.importorskip("sonolus_backend")
    from sonolus.backend.node import FunctionNode
    from sonolus.backend.ops import Op

    metrics = _load_metrics_module()
    # Add(1, Multiply(2.5, 3), Abs(Negate(1))) — the repeated constant 1 dedups in the DAG.
    tree = FunctionNode(
        Op.Add,
        (
            1,
            FunctionNode(Op.Multiply, (2.5, 3)),
            FunctionNode(Op.Abs, (FunctionNode(Op.Negate, (1,)),)),
        ),
    )
    nested, static_nodes = metrics.legacy_node_to_nested(tree)
    assert static_nodes == 8
    engine_nodes = sonolus_backend.EngineNodes(nested)
    assert engine_nodes.tree_node_count() == 8
    assert engine_nodes.output_node_count() == 7  # the second `1` dedups
    interp = sonolus_backend.Interpreter(seed=0)
    result = interp.run(engine_nodes)
    assert result == 1 + 2.5 * 3 + abs(-1)
    assert math.isfinite(result)
