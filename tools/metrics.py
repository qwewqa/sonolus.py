"""Quality-metrics collectors and frozen-oracle baselines for the Rust backend port (PORT.md T2.4).

Subcommands (run from the repo root; the ``sonolus_backend`` extension must be installed,
``uv run maturin develop -m rust/sonolus-backend-py/Cargo.toml``):

``uv run python tools/metrics.py baseline``
    Measures the FROZEN Python backend over every pydori callback (the same enumeration
    as ``tests/regressions/test_project.py`` / ``tests/backend/test_pipeline.py``: all
    modes, dev and non-dev runtime checks) and writes the ratchet contract files:

    - ``rust/baselines/python-standard.json``: per-callback ``static_nodes`` (engine-node
      tree size, pre-dedup), ``dag_size`` (output-node count after DAG dedup), dynamic
      ``eval_count``/``dispatch_count`` (measured on the RUST interpreter — see below),
      and ``wall_time_ms`` (median ``STANDARD_PASSES`` compile time), plus aggregates.
    - ``rust/baselines/python-fast.json``: median ``FAST_PASSES`` compile times — the
      G-P1 reference (the dev-default time the Rust backend must beat).

    ``--check-determinism`` recomputes everything and compares against the files on disk
    with the volatile fields (wall times, timestamps, machine info) stripped; nothing is
    written. Everything except wall times is deterministic.

``uv run python tools/metrics.py report``
    Compiles the same pydori callbacks through the RUST pipeline (``--level``, default
    ``standard``), measures the identical metrics under the identical seeded memory/RNG,
    and prints the comparison against ``python-standard.json``: per-metric aggregate
    ratios and the >10%-worse-on-eval-count callback list — the artifact the G3.x gates
    read. ``--ratchet`` makes the exit code enforce the G3.3 ratchet (aggregate parity
    and no callback >10% worse on dynamic eval count).

``uv run python tools/metrics.py corpus``
    Rust-side metrics over the mini-corpus (``rust/testdata/``) entries that have I/O
    vectors: compile each frontend CFG at ``--level``, replay every stored vector
    (inputs + RNG tape), record eval/dispatch counts. Compares against
    ``rust/baselines/rust-corpus.json`` (``--update`` rewrites it). This is the corpus
    half of the wave ratchet, compared Rust-vs-Rust across waves.

Baseline scoping (the resolved design): the frozen-oracle baseline covers **pydori
callbacks only**. Mini-corpus entries cannot be measured on the Python side — Python
cannot decode ``.scfg`` files and the original CFG objects no longer exist — so corpus
rows are tracked Rust-vs-Rust across waves instead. The G3.3 ratchet baseline therefore
equals the pydori per-callback numbers, which is what PORT.md §8's "no callback >10%
worse" means.

Dynamic metrics methodology: BOTH backends' compiled node trees run on the RUST
interpreter (legacy ``FunctionNode`` trees are converted via nested ``(op_id, [args])``
data into ``sonolus_backend.EngineNodes``), with identical seeded initial memory,
identical RNG seed, and an eval budget as the termination backstop. ``eval_count`` is
one increment per node evaluation including constants; ``dispatch_count`` is one per
non-tail JumpLoop round trip (see ``rust/sonolus-backend-core/src/interpret.rs``).

Seeded memory fill (bit-exact pure-Python mirror of
``rust/sonolus-backend-core/src/diff.rs::build_memory`` — pinned bit-identical by
``tests/backend/test_metrics_baseline.py``, and trivially reusable from Rust since the
Rust implementation is the original):

1. Discover the runtime blocks the **frontend** CFG can read: every concrete integer
   ``BlockPlace.block`` id (walking nested places), plus the value of any ``IRConst``
   integer first argument of a memory-touching op (an op that is not ``pure`` and is not
   ``Random``/``RandomInteger``). Always include ROM (3000); never include the temp
   runtime block (10000, slot layout is pipeline-specific). Sort ascending.
2. For each block, seed a private SplitMix64 with
   ``memory_seed XOR (block * 0x9E3779B97F4A7C15 mod 2^64)`` and fill 32 cells from the
   documented value mix (small non-negative ints as the majority, 0/-1/65535, small
   negatives, integral floats, floats in [-1, 1), full-range floats). ROM starts with
   ``NaN``/``+inf``/``-inf`` at indices 0..2, exactly like the runtime provides.

Unfilled cells read the interpreter default ``-1.0`` identically on both sides.

Runaway handling: a callback whose run exceeds the eval budget under one seed pair is
retried with the next documented ``SEED_ATTEMPTS`` pair; if every attempt runs away, the
row records ``dynamic: null`` with a reason. The successful attempt's index is recorded
per row and reused by ``report`` so both backends are always measured under identical
inputs.

Runtime-only op stubbing (``--stub-runtime-ops`` on ``baseline`` and ``report``, off by
default): enables the Rust interpreter's METRICS-only stub mode
(``Interpreter::set_stub_runtime_ops`` — see
``rust/sonolus-backend-core/src/interpret.rs`` for the rule). Runtime-only ops
(Draw/BeatToTime/ExportValue/...) evaluate their arguments in order and produce ``0.0``
instead of trapping, so draw-heavy callbacks measure their full dynamics instead of just
the prologue before the first runtime-only op. The flag applies to the dynamic
measurement of BOTH backends (the legacy-baseline path under ``baseline`` and the Rust
path under ``report``). A baseline generated with stubs on records
``"stub_runtime_ops": true`` in its metadata so the file self-describes; files generated
with stubs off omit the key (absence means off, keeping pre-flag baselines comparable),
and ``report`` warns when its flag does not match the baseline's. Rows that exceed the
eval budget under stub mode (a previously-trapping callback may now run long or loop on
stubbed-zero conditions) are handled exactly like any other budget-exceeded row
(seed-attempt retry, then ``dynamic: null``), and both subcommands report how many rows
ran to completion vs budget-exceeded vs trapped.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import struct
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "test_projects"))

sys.setrecursionlimit(10_000)  # Matches the legacy CLI (deep IR trees in frozen passes).

import sonolus_backend

from sonolus.backend.encode import encode_cfg
from sonolus.backend.finalize import cfg_to_engine_node
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.optimize.optimize import FAST_PASSES, STANDARD_PASSES
from sonolus.backend.optimize.passes import OptimizerConfig, run_passes
from sonolus.backend.place import BlockPlace, TempBlock
from sonolus.build.node import OutputNodeGenerator

SCHEMA_VERSION = 1
BASELINE_DIR = REPO_ROOT / "rust" / "baselines"
PYTHON_STANDARD_FILE = BASELINE_DIR / "python-standard.json"
PYTHON_FAST_FILE = BASELINE_DIR / "python-fast.json"
RUST_CORPUS_FILE = BASELINE_DIR / "rust-corpus.json"
TESTDATA_DIR = REPO_ROOT / "rust" / "testdata"

#: Cells filled per discovered block (mirrors diff.rs FILL_LEN).
FILL_LEN = 32
ROM_BLOCK = 3000
TEMP_RUNTIME_BLOCK = 10000

#: Documented (memory_seed, rng_seed) pairs tried in order when a run exceeds the eval
#: budget. The index of the pair that succeeded is recorded in each baseline row and
#: replayed by `report` so both backends always see identical inputs.
SEED_ATTEMPTS = (
    (0x5EED_0001, 0x0123_4567),
    (0x5EED_0002, 0x89AB_CDEF),
    (0x5EED_0003, 0x4242_4242),
)

#: Termination backstop for dynamic runs (well above any bounded pydori callback; the
#: Rust pipeline pre-W1 evaluates a few times more nodes than legacy standard, so the
#: headroom must cover both sides).
EVAL_BUDGET = 200_000_000

#: Wall-time runs per callback per pass list (median is recorded).
DEFAULT_TIMING_RUNS = 5

#: Fields stripped before determinism comparison (everything else must be identical).
VOLATILE_KEYS = frozenset({"generated_at", "machine", "wall_time_ms", "wall_time_ms_total", "duration_s"})

_MASK64 = (1 << 64) - 1
_GOLDEN = 0x9E37_79B9_7F4A_7C15
_F64_SCALE = 1.0 / (1 << 53)

_OP_IDS = {op: i for i, op in enumerate(Op)}

#: Non-pure ops that never touch memory (mirrors diff.rs / effects.rs NEITHER_RNG; every
#: other non-pure op is treated as memory-touching, matching `op_effects`).
_RNG_OPS = frozenset({Op.Random, Op.RandomInteger})


class MetricsError(Exception):
    """A metrics invariant failed (converter mismatch, missing baseline, ...)."""


# ----------------------------------------------------------------------------------
# Seeded memory fill (pure-Python mirror of diff.rs)
# ----------------------------------------------------------------------------------


class SplitMix64:
    """SplitMix64 (Steele, Lea, Flood 2014); bit-exact mirror of the diff.rs copy."""

    __slots__ = ("state",)

    def __init__(self, seed: int):
        self.state = seed & _MASK64

    def next_u64(self) -> int:
        self.state = (self.state + _GOLDEN) & _MASK64
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58_476D_1CE4_E5B9) & _MASK64
        z = ((z ^ (z >> 27)) * 0x94D0_49BB_1331_11EB) & _MASK64
        return (z ^ (z >> 31)) & _MASK64

    def next_f64(self) -> float:
        return (self.next_u64() >> 11) * _F64_SCALE


def _mixed_value(rng: SplitMix64) -> float:
    """One value of the documented mix (bit-exact mirror of diff.rs `mixed_value`)."""
    sel = rng.next_u64() % 100
    if sel <= 34:
        return float(rng.next_u64() % 16)  # small ints
    if sel <= 44:
        return 0.0
    if sel <= 52:
        return -1.0
    if sel <= 57:
        return 65535.0
    if sel <= 64:
        return -(float(rng.next_u64() % 16) + 1.0)  # small negatives
    if sel <= 76:
        return float(rng.next_u64() % 20_001) - 10_000.0  # integral floats
    if sel <= 89:
        return rng.next_f64() * 2.0 - 1.0  # [-1, 1)
    # Full-range floats: random sign and binary exponent in [-60, 60].
    exp = (rng.next_u64() % 121) - 60
    sign = 1.0 if rng.next_u64() & 1 == 0 else -1.0
    return sign * rng.next_f64() * 2.0**exp


def discover_read_blocks(entry: BasicBlock) -> list[int]:
    """The runtime blocks a frontend CFG can read (see the module docstring, step 1).

    Pure-Python mirror of ``diff.rs::discover_read_blocks`` over the live (pre-pass)
    frontend CFG instead of the decoded encoding; iterative.
    """
    ids: set[int] = {ROM_BLOCK}

    def scan(root: object) -> None:
        stack = [root]
        while stack:
            cur = stack.pop()
            if isinstance(cur, IRPureInstr | IRInstr):
                op = cur.op
                if (
                    not op.pure
                    and op not in _RNG_OPS
                    and cur.args
                    and isinstance(cur.args[0], IRConst)
                    and isinstance(cur.args[0].value, int)
                ):
                    ids.add(int(cur.args[0].value))
                stack.extend(cur.args)
            elif isinstance(cur, IRGet):
                stack.append(cur.place)
            elif isinstance(cur, IRSet):
                stack.append(cur.place)
                stack.append(cur.value)
            elif isinstance(cur, BlockPlace):
                block = cur.block
                if isinstance(block, TempBlock):
                    pass
                elif isinstance(block, BlockPlace):
                    stack.append(block)
                elif isinstance(block, int):
                    ids.add(int(block))
                if isinstance(cur.index, BlockPlace):
                    stack.append(cur.index)

    for block in traverse_cfg_preorder(entry):
        for statement in block.statements:
            scan(statement)
        scan(block.test)
    ids.discard(TEMP_RUNTIME_BLOCK)
    return sorted(ids)


def build_fill(read_blocks: list[int], memory_seed: int) -> list[tuple[int, list[float]]]:
    """The seeded initial memory for the given blocks (mirror of diff.rs `build_memory`)."""
    result = []
    for block in read_blocks:
        rng = SplitMix64(memory_seed ^ ((block & _MASK64) * _GOLDEN & _MASK64))
        values: list[float] = []
        if block == ROM_BLOCK:
            values.extend((math.nan, math.inf, -math.inf))
        while len(values) < FILL_LEN:
            values.append(_mixed_value(rng))
        result.append((block, values))
    return result


# ----------------------------------------------------------------------------------
# Pydori callback enumeration (mirrors tests/backend/test_pipeline.py)
# ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class CallbackSpec:
    label: str
    mode: Mode
    callback_name: str
    trace: Callable[[], BasicBlock]


def iter_pydori_callbacks() -> list[CallbackSpec]:
    """Enumerates every pydori callback with a re-traceable factory per callback.

    All modes, dev and non-dev runtime checks (mirrors tests/backend/test_pipeline.py).
    A fresh trace per measurement is required: the legacy passes mutate CFGs in place.
    """
    import pydori.project

    from sonolus.build.compile import callback_to_cfg
    from sonolus.script.internal.callbacks import (
        navigate_callback,
        preprocess_callback,
        update_callback,
        update_spawn_callback,
    )
    from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks

    engine = pydori.project.project.engine.data
    mode_specs = [
        (Mode.PLAY, engine.play.archetypes, None),
        (Mode.WATCH, engine.watch.archetypes, [(update_spawn_callback, engine.watch.update_spawn)]),
        (Mode.PREVIEW, engine.preview.archetypes, None),
        (
            Mode.TUTORIAL,
            None,
            [
                (preprocess_callback, engine.tutorial.preprocess),
                (navigate_callback, engine.tutorial.navigate),
                (update_callback, engine.tutorial.update),
            ],
        ),
    ]

    def make_trace(cb, cb_name, archetype, runtime_checks, mode, archetypes):
        def trace() -> BasicBlock:
            project_state = ProjectContextState(runtime_checks=runtime_checks)
            mode_state = ModeContextState(
                mode,
                {a: i for i, a in enumerate(archetypes)} if archetypes is not None else None,
            )
            return callback_to_cfg(project_state, mode_state, cb, cb_name, archetype)

        return trace

    specs: list[CallbackSpec] = []
    for mode, archetypes, global_callbacks in mode_specs:
        for dev in (False, True):
            suffix = "_dev" if dev else ""
            runtime_checks = RuntimeChecks.NOTIFY_AND_TERMINATE if dev else RuntimeChecks.NONE
            for archetype in archetypes or []:
                archetype._init_fields()
                callback_items = [
                    (cb_name, cb_info, getattr(archetype, cb_name))
                    for cb_name, cb_info in archetype._supported_callbacks_.items()
                    if getattr(archetype, cb_name) not in archetype._default_callbacks_
                ]
                for cb_name, cb_info, cb in callback_items:
                    specs.append(
                        CallbackSpec(
                            label=f"{mode.name.lower()}/{archetype.__name__}.{cb_name}{suffix}",
                            mode=mode,
                            callback_name=cb_info.name,
                            trace=make_trace(cb, cb_info.name, archetype, runtime_checks, mode, archetypes),
                        )
                    )
            for cb_info, cb in global_callbacks or []:
                specs.append(
                    CallbackSpec(
                        label=f"{mode.name.lower()}/global.{cb_info.name}{suffix}",
                        mode=mode,
                        callback_name=cb_info.name,
                        trace=make_trace(cb, cb_info.name, None, runtime_checks, mode, archetypes),
                    )
                )
    labels = [s.label for s in specs]
    if len(set(labels)) != len(labels):
        raise MetricsError("pydori callback labels are not unique")
    return specs


# ----------------------------------------------------------------------------------
# Legacy node tree -> Rust EngineNodes
# ----------------------------------------------------------------------------------


def legacy_node_to_nested(root) -> tuple[Any, int]:
    """Converts a legacy ``EngineNode`` tree into nested ``(op_id, [args])`` data.

    The result is accepted by ``sonolus_backend.EngineNodes``; the walk is iterative
    (legacy trees can be deep). Returns ``(nested_data, tree_node_count)``; the count is
    the ``static_nodes`` metric (one per tree-node occurrence — legacy trees are strict
    object trees).
    """
    items: list[Any] = []
    stack: list[tuple[Any, int | None, int | None]] = [(root, None, None)]
    while stack:
        node, parent, slot = stack.pop()
        index = len(items)
        if parent is not None:
            items[parent][1][slot] = index
        if isinstance(node, FunctionNode):
            children: list[Any] = [None] * len(node.args)
            items.append((_OP_IDS[node.func], children))
            for i, arg in enumerate(node.args):
                stack.append((arg, index, i))
        elif isinstance(node, int | float):
            items.append(node)
        else:
            raise MetricsError(f"unexpected engine-node leaf type: {type(node).__name__}")
    resolved: list[Any] = [None] * len(items)
    for i in range(len(items) - 1, -1, -1):
        item = items[i]
        if isinstance(item, tuple):
            op_id, children = item
            resolved[i] = (op_id, [resolved[c] for c in children])
        else:
            resolved[i] = item
    return resolved[0], len(items)


def compile_legacy(spec: CallbackSpec, passes) -> tuple[Any, dict[str, int], list[int]]:
    """Traces and compiles a callback with the frozen Python backend.

    Returns ``(rust_engine_nodes, {"static_nodes", "dag_size"}, read_blocks)`` where the
    engine nodes are the legacy tree converted for the Rust interpreter. The converter
    is pinned against the Rust counters (tree count and output-node count must agree).
    """
    cfg = spec.trace()
    read_blocks = discover_read_blocks(cfg)
    config = OptimizerConfig(mode=spec.mode, callback=spec.callback_name)
    optimized = run_passes(cfg, passes, config)
    node = cfg_to_engine_node(optimized)

    generator = OutputNodeGenerator()
    generator.add(node)
    dag_size = len(generator.get())

    nested, static_nodes = legacy_node_to_nested(node)
    engine_nodes = sonolus_backend.EngineNodes(nested)
    if engine_nodes.tree_node_count() != static_nodes:
        raise MetricsError(
            f"{spec.label}: converter tree count {static_nodes} != rust {engine_nodes.tree_node_count()}"
        )
    if engine_nodes.output_node_count() != dag_size:
        raise MetricsError(f"{spec.label}: legacy dag size {dag_size} != rust {engine_nodes.output_node_count()}")
    return engine_nodes, {"static_nodes": static_nodes, "dag_size": dag_size}, read_blocks


# ----------------------------------------------------------------------------------
# Dynamic runs (Rust interpreter; identical setup for both backends)
# ----------------------------------------------------------------------------------


def _float_bits(value: float) -> str:
    return f"0x{struct.unpack('<Q', struct.pack('<d', value))[0]:016x}"


def _bits_to_float(bits: str) -> float:
    return struct.unpack("<d", struct.pack("<Q", int(bits, 16)))[0]


def _results_match(a_bits: str, b_bits: str) -> bool:
    """Raw-bit equality, NaN-aware and zero-sign-tolerant.

    Any NaN equals any NaN and +0.0 == -0.0 (the documented legacy contract; mirrors
    diff.rs `values_match`).
    """
    a = _bits_to_float(a_bits)
    b = _bits_to_float(b_bits)
    if math.isnan(a) and math.isnan(b):
        return True
    if a == 0.0 and b == 0.0:
        return True
    return a_bits == b_bits


def run_dynamic(
    engine_nodes,
    memory: list[tuple[int, list[float]]],
    rng_seed: int,
    eval_budget: int,
    stub_runtime_ops: bool = False,
):
    """Runs a node tree under seeded memory and collects the dynamic counters.

    Returns a dynamic row dict, or None when the eval budget was exceeded (a cutoff is
    not a semantic fact). ``stub_runtime_ops`` enables the interpreter's metrics-only
    runtime-op stub mode (see the module docstring).
    """
    interp = sonolus_backend.Interpreter(seed=rng_seed)
    interp.set_eval_budget(eval_budget)
    if stub_runtime_ops:
        interp.set_stub_runtime_ops(True)
    for block, values in memory:
        interp.set_block(block, values)
    try:
        result = interp.run(engine_nodes)
        outcome: dict[str, Any] = {"status": "ok", "result": _float_bits(result)}
    except RuntimeError as e:
        if str(e).startswith("eval budget exceeded"):
            return None
        outcome = {"status": "error", "error_type": type(e).__name__, "error": str(e)}
    except (AssertionError, ZeroDivisionError, ValueError, OverflowError, IndexError, NotImplementedError) as e:
        outcome = {"status": "error", "error_type": type(e).__name__, "error": str(e)}
    return {
        "eval_count": interp.eval_count,
        "dispatch_count": interp.dispatch_count,
        "log_len": len(interp.log),
        "outcome": outcome,
    }


def run_dynamic_with_attempts(
    engine_nodes,
    read_blocks: list[int],
    seed_index: int | None = None,
    stub_runtime_ops: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    """Runs under the documented seed attempts (or one specific attempt).

    Returns ``(dynamic_row, None)`` on success or ``(None, reason)`` when every tried
    attempt exceeded the eval budget.
    """
    indices = range(len(SEED_ATTEMPTS)) if seed_index is None else [seed_index]
    for i in indices:
        memory_seed, rng_seed = SEED_ATTEMPTS[i]
        memory = build_fill(read_blocks, memory_seed)
        row = run_dynamic(engine_nodes, memory, rng_seed, EVAL_BUDGET, stub_runtime_ops)
        if row is not None:
            row = {"seed_index": i, **row}
            return row, None
    tried = ", ".join(str(i) for i in indices)
    return None, f"eval budget ({EVAL_BUDGET}) exceeded for seed attempt(s) {tried}"


def _outcomes_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a["status"] != b["status"]:
        return False
    if a["status"] == "ok":
        return _results_match(a["result"], b["result"])
    return a["error_type"] == b["error_type"] and a["error"] == b["error"]


def _row_coverage(dynamics: list[dict[str, Any] | None]) -> tuple[int, int, int]:
    """``(completed, budget_exceeded, trapped)`` counts over dynamic rows.

    ``None`` dynamics are budget-exceeded rows (the only way a row goes null); error
    outcomes are trapped rows (the run stopped at an erroring op).
    """
    completed = sum(1 for d in dynamics if d is not None and d["outcome"]["status"] == "ok")
    budget_exceeded = sum(1 for d in dynamics if d is None)
    trapped = sum(1 for d in dynamics if d is not None and d["outcome"]["status"] == "error")
    return completed, budget_exceeded, trapped


# ----------------------------------------------------------------------------------
# Wall-time measurement (frozen Python backend)
# ----------------------------------------------------------------------------------


def measure_wall_time_ms(spec: CallbackSpec, passes, runs: int) -> float:
    """Median frozen-backend compile wall time over ``runs`` fresh traces, in ms.

    Times ``run_passes`` through ``cfg_to_engine_node`` plus ``OutputNodeGenerator``
    (tracing excluded; the generator is per-callback, isolating each callback's own
    dedup cost — production shares one generator per mode).
    """
    config = OptimizerConfig(mode=spec.mode, callback=spec.callback_name)
    times = []
    for _ in range(runs):
        cfg = spec.trace()
        start = time.perf_counter()
        optimized = run_passes(cfg, passes, config)
        node = cfg_to_engine_node(optimized)
        generator = OutputNodeGenerator()
        generator.add(node)
        times.append((time.perf_counter() - start) * 1000.0)
    return round(statistics.median(times), 3)


# ----------------------------------------------------------------------------------
# Shared metadata / JSON helpers
# ----------------------------------------------------------------------------------


def _machine_info() -> dict[str, str]:
    import os

    return {
        "cpu": os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor() or "unknown",
        "platform": platform.platform(),
        "python": platform.python_version(),
    }


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _common_metadata() -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "machine": _machine_info(),
        "commit": _git_commit(),
        "project": "pydori",
        "fill": {
            "algorithm": "splitmix64-mix-v1 (diff.rs build_memory)",
            "fill_len": FILL_LEN,
            "rom_block": ROM_BLOCK,
            "temp_block_excluded": TEMP_RUNTIME_BLOCK,
        },
        "seed_attempts": [list(pair) for pair in SEED_ATTEMPTS],
        "eval_budget": EVAL_BUDGET,
    }


def strip_volatile(doc: Any) -> Any:
    """Removes the volatile (timing/timestamp/machine) fields for determinism diffs."""
    if isinstance(doc, dict):
        return {k: strip_volatile(v) for k, v in doc.items() if k not in VOLATILE_KEYS}
    if isinstance(doc, list):
        return [strip_volatile(v) for v in doc]
    return doc


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise MetricsError(f"missing baseline file: {path} (run `uv run python tools/metrics.py baseline`)")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------------------
# baseline subcommand
# ----------------------------------------------------------------------------------


def collect_python_baselines(
    runs: int, limit: int | None = None, stub_runtime_ops: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Measures the frozen Python backend over pydori; returns the two baseline docs."""
    started = time.perf_counter()
    specs = iter_pydori_callbacks()
    if limit is not None:
        specs = specs[:limit]
    specs = sorted(specs, key=lambda s: s.label)

    standard_rows = []
    fast_rows = []
    for n, spec in enumerate(specs, 1):
        print(f"[{n}/{len(specs)}] {spec.label}", flush=True)
        engine_nodes, static, read_blocks = compile_legacy(spec, STANDARD_PASSES)
        dynamic, null_reason = run_dynamic_with_attempts(
            engine_nodes, read_blocks, stub_runtime_ops=stub_runtime_ops
        )
        row: dict[str, Any] = {
            "label": spec.label,
            "mode": spec.mode.name.lower(),
            "callback": spec.callback_name,
            "static_nodes": static["static_nodes"],
            "dag_size": static["dag_size"],
            "dynamic": dynamic,
        }
        if dynamic is None:
            row["dynamic_null_reason"] = null_reason
        row["wall_time_ms"] = measure_wall_time_ms(spec, STANDARD_PASSES, runs)
        standard_rows.append(row)
        fast_rows.append(
            {
                "label": spec.label,
                "wall_time_ms": measure_wall_time_ms(spec, FAST_PASSES, runs),
            }
        )

    dynamic_rows = [r for r in standard_rows if r["dynamic"] is not None]
    standard_doc = {
        **_common_metadata(),
        # Self-description (the dynamics contract): present and true only when the
        # metrics-only runtime-op stub mode was on; absent means off, keeping files
        # generated before the flag existed comparable.
        **({"stub_runtime_ops": True} if stub_runtime_ops else {}),
        "kind": "python-standard",
        "level": "standard",
        "passes": [type(p).__name__ for p in STANDARD_PASSES],
        "timing_runs": runs,
        "callback_count": len(standard_rows),
        "aggregates": {
            "static_nodes": sum(r["static_nodes"] for r in standard_rows),
            "dag_size": sum(r["dag_size"] for r in standard_rows),
            "eval_count": sum(r["dynamic"]["eval_count"] for r in dynamic_rows),
            "dispatch_count": sum(r["dynamic"]["dispatch_count"] for r in dynamic_rows),
            "dynamic_rows": len(dynamic_rows),
            "dynamic_null": [r["label"] for r in standard_rows if r["dynamic"] is None],
            "wall_time_ms_total": round(sum(r["wall_time_ms"] for r in standard_rows), 3),
        },
        "rows": standard_rows,
        "duration_s": round(time.perf_counter() - started, 1),
    }
    fast_doc = {
        **_common_metadata(),
        "kind": "python-fast",
        "level": "fast",
        "passes": [type(p).__name__ for p in FAST_PASSES],
        "timing_runs": runs,
        "callback_count": len(fast_rows),
        "aggregates": {
            "wall_time_ms_total": round(sum(r["wall_time_ms"] for r in fast_rows), 3),
        },
        "rows": fast_rows,
    }
    return standard_doc, fast_doc


def cmd_baseline(args) -> int:
    standard_doc, fast_doc = collect_python_baselines(args.runs, args.limit, args.stub_runtime_ops)
    if args.check_determinism:
        ok = True
        for path, doc in ((PYTHON_STANDARD_FILE, standard_doc), (PYTHON_FAST_FILE, fast_doc)):
            stored = strip_volatile(_load_json(path))
            fresh = strip_volatile(doc)
            if stored == fresh:
                print(f"determinism check PASSED: {path.name} (volatile fields stripped)")
            else:
                ok = False
                print(f"determinism check FAILED: {path.name} differs beyond volatile fields")
                _diff_summary(stored, fresh)
        return 0 if ok else 1
    _write_json(PYTHON_STANDARD_FILE, standard_doc)
    _write_json(PYTHON_FAST_FILE, fast_doc)
    agg = standard_doc["aggregates"]
    print(f"\nwrote {PYTHON_STANDARD_FILE}")
    print(f"wrote {PYTHON_FAST_FILE}")
    print(f"callbacks: {standard_doc['callback_count']}")
    print(
        f"python-standard aggregates: static_nodes={agg['static_nodes']} dag_size={agg['dag_size']} "
        f"eval_count={agg['eval_count']} dispatch_count={agg['dispatch_count']} "
        f"(dynamic rows: {agg['dynamic_rows']}, null: {len(agg['dynamic_null'])})"
    )
    completed, budget_exceeded, trapped = _row_coverage([r["dynamic"] for r in standard_doc["rows"]])
    print(
        f"dynamic row coverage (stub_runtime_ops={args.stub_runtime_ops}): "
        f"completed={completed} budget-exceeded={budget_exceeded} trapped={trapped}"
    )
    print(f"python-standard wall total: {agg['wall_time_ms_total']:.1f} ms")
    print(f"python-fast wall total: {fast_doc['aggregates']['wall_time_ms_total']:.1f} ms (G-P1 reference)")
    return 0


def _diff_summary(stored: Any, fresh: Any, path: str = "$") -> None:
    """Prints the first few structural differences between two stripped docs."""
    diffs: list[str] = []

    def walk(a: Any, b: Any, where: str) -> None:
        if len(diffs) >= 10:
            return
        if type(a) is not type(b):
            diffs.append(f"{where}: type {type(a).__name__} != {type(b).__name__}")
        elif isinstance(a, dict):
            for k in a.keys() | b.keys():
                if k not in a or k not in b:
                    diffs.append(f"{where}.{k}: present on one side only")
                else:
                    walk(a[k], b[k], f"{where}.{k}")
        elif isinstance(a, list):
            if len(a) != len(b):
                diffs.append(f"{where}: length {len(a)} != {len(b)}")
            for i, (x, y) in enumerate(zip(a, b, strict=False)):
                walk(x, y, f"{where}[{i}]")
        elif a != b:
            diffs.append(f"{where}: {a!r} != {b!r}")

    walk(stored, fresh, path)
    for d in diffs[:10]:
        print(f"  {d}")


# ----------------------------------------------------------------------------------
# report subcommand (Rust vs python-standard baseline)
# ----------------------------------------------------------------------------------


def collect_rust_row(
    spec: CallbackSpec,
    level: str,
    baseline_dynamic: dict[str, Any] | None,
    stub_runtime_ops: bool = False,
):
    """Measures one pydori callback compiled through the Rust pipeline.

    The dynamic run uses the baseline row's recorded seed attempt so both backends see
    identical inputs.
    """
    cfg = spec.trace()
    read_blocks = discover_read_blocks(cfg)
    data = encode_cfg(cfg)
    engine_nodes, stats = sonolus_backend.run_pipeline_stats(data, level)
    dynamic = None
    null_reason = None
    if baseline_dynamic is not None:
        dynamic, null_reason = run_dynamic_with_attempts(
            engine_nodes,
            read_blocks,
            seed_index=baseline_dynamic["seed_index"],
            stub_runtime_ops=stub_runtime_ops,
        )
    return {
        "static_nodes": stats["static_nodes"],
        "dag_size": stats["dag_size"],
        "dynamic": dynamic,
        "dynamic_null_reason": null_reason,
    }


def cmd_report(args) -> int:
    baseline = _load_json(PYTHON_STANDARD_FILE)
    fast_doc = _load_json(PYTHON_FAST_FILE)
    baseline_stub = bool(baseline.get("stub_runtime_ops", False))
    if baseline_stub != args.stub_runtime_ops:
        print(
            f"WARNING: --stub-runtime-ops={args.stub_runtime_ops} but the stored baseline was generated "
            f"with stub_runtime_ops={baseline_stub}; outcome comparisons cross stub modes (previously "
            f"trapping rows show as behavior mismatches). Regenerate the baseline with the matching "
            f"flag for a valid comparison."
        )
    specs = {s.label: s for s in iter_pydori_callbacks()}
    rows = baseline["rows"]
    if args.limit is not None:
        rows = rows[: args.limit]

    missing = [r["label"] for r in rows if r["label"] not in specs]
    if missing:
        raise MetricsError(f"baseline rows not in the current pydori enumeration: {missing[:5]} ...")

    totals = {
        "static_nodes": [0, 0],  # [python, rust]
        "dag_size": [0, 0],
        "eval_count": [0, 0],
        "dispatch_count": [0, 0],
    }
    compared_dynamic = 0
    behavior_mismatches: list[str] = []
    rust_runaways: list[str] = []
    eval_ratios: list[tuple[float, str, int, int]] = []
    rust_dynamics: list[dict[str, Any] | None] = []

    for n, row in enumerate(rows, 1):
        label = row["label"]
        print(f"[{n}/{len(rows)}] {label}", flush=True)
        rust = collect_rust_row(specs[label], args.level, row["dynamic"], args.stub_runtime_ops)
        totals["static_nodes"][0] += row["static_nodes"]
        totals["static_nodes"][1] += rust["static_nodes"]
        totals["dag_size"][0] += row["dag_size"]
        totals["dag_size"][1] += rust["dag_size"]
        if row["dynamic"] is None:
            continue
        rust_dynamics.append(rust["dynamic"])
        if rust["dynamic"] is None:
            rust_runaways.append(f"{label}: {rust['dynamic_null_reason']}")
            continue
        py_dyn = row["dynamic"]
        rust_dyn = rust["dynamic"]
        if not _outcomes_match(py_dyn["outcome"], rust_dyn["outcome"]) or py_dyn["log_len"] != rust_dyn["log_len"]:
            behavior_mismatches.append(
                f"{label}: python {py_dyn['outcome']} (log {py_dyn['log_len']}) "
                f"!= rust {rust_dyn['outcome']} (log {rust_dyn['log_len']})"
            )
            continue
        compared_dynamic += 1
        totals["eval_count"][0] += py_dyn["eval_count"]
        totals["eval_count"][1] += rust_dyn["eval_count"]
        totals["dispatch_count"][0] += py_dyn["dispatch_count"]
        totals["dispatch_count"][1] += rust_dyn["dispatch_count"]
        ratio = rust_dyn["eval_count"] / py_dyn["eval_count"] if py_dyn["eval_count"] else math.inf
        eval_ratios.append((ratio, label, py_dyn["eval_count"], rust_dyn["eval_count"]))

    print()
    print("Rust backend metrics vs frozen Python `standard` baseline (pydori)")
    print(f"  baseline: {PYTHON_STANDARD_FILE.name} (commit {baseline.get('commit')}, {baseline.get('generated_at')})")
    print(f"  rust: level={args.level}, backend {sonolus_backend.backend_version()}")
    print(
        f"  callbacks: {len(rows)} | dynamic compared: {compared_dynamic} | "
        f"python dynamic-null: {len(baseline['aggregates']['dynamic_null'])} | "
        f"rust runaway: {len(rust_runaways)} | behavior mismatch: {len(behavior_mismatches)}"
    )
    print()
    print(f"  {'metric':<16} {'python-std':>14} {'rust':>14} {'rust/python':>12}")
    for metric, (py_total, rust_total) in totals.items():
        ratio = rust_total / py_total if py_total else math.inf
        print(f"  {metric:<16} {py_total:>14} {rust_total:>14} {ratio:>12.3f}")
    print()
    base_cov = _row_coverage([r["dynamic"] for r in rows])
    rust_cov = _row_coverage(rust_dynamics)
    print("  dynamic row coverage (completed / budget-exceeded / trapped):")
    print(f"    python baseline (stub_runtime_ops={baseline_stub}): {base_cov[0]} / {base_cov[1]} / {base_cov[2]}")
    print(
        f"    rust this run (stub_runtime_ops={args.stub_runtime_ops}): "
        f"{rust_cov[0]} / {rust_cov[1]} / {rust_cov[2]} (of {len(rust_dynamics)} measured)"
    )
    print()

    worse = sorted((r for r in eval_ratios if r[0] > 1.10), reverse=True)
    print(f"  callbacks >10% worse on eval_count: {len(worse)} of {compared_dynamic}")
    for ratio, label, py_eval, rust_eval in worse[: args.worst]:
        print(f"    {label:<60} {py_eval:>12} -> {rust_eval:>12}  x{ratio:.3f}")
    if len(worse) > args.worst:
        print(f"    ... and {len(worse) - args.worst} more")
    for title, entries in (("rust runaways", rust_runaways), ("behavior mismatches", behavior_mismatches)):
        if entries:
            print(f"  {title}:")
            for e in entries[:10]:
                print(f"    {e}")
    print()
    print(
        f"  python-fast wall total: {fast_doc['aggregates']['wall_time_ms_total']:.1f} ms "
        f"(G-P1 reference); python-standard wall total: "
        f"{baseline['aggregates']['wall_time_ms_total']:.1f} ms"
    )

    if args.ratchet:
        aggregate_ok = all(rust_total <= py_total for py_total, rust_total in totals.values())
        ratchet_ok = aggregate_ok and not worse and not behavior_mismatches and not rust_runaways
        print(f"\n  ratchet (G3.3): {'PASS' if ratchet_ok else 'FAIL'}")
        return 0 if ratchet_ok else 1
    return 0


# ----------------------------------------------------------------------------------
# corpus subcommand (Rust-vs-Rust across waves)
# ----------------------------------------------------------------------------------


def _decode_vector_value(value) -> float:
    if isinstance(value, str):
        return _bits_to_float(value)
    return float(value)


def collect_corpus(level: str) -> dict[str, Any]:
    """Rust-side metrics over every mini-corpus entry that has I/O vectors."""
    manifest = json.loads((TESTDATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    eval_total = 0
    dispatch_total = 0
    vector_total = 0
    for entry in manifest["entries"]:
        if entry["vectors"] == 0:
            continue
        digest = entry["hash"]
        data = (TESTDATA_DIR / "cfgs" / f"{digest}.scfg").read_bytes()
        engine_nodes, stats = sonolus_backend.run_pipeline_stats(data, level)
        vec_doc = json.loads((TESTDATA_DIR / "vectors" / f"{digest}.json").read_text(encoding="utf-8"))
        vec_rows = []
        for i, vector in enumerate(vec_doc["vectors"]):
            interp = sonolus_backend.Interpreter(seed=0)
            interp.set_eval_budget(EVAL_BUDGET)
            interp.set_rng_tape([_decode_vector_value(v[3]) for v in vector["rng"]])
            for block, values in vector["inputs"]:
                interp.set_block(block, [_decode_vector_value(v) for v in values])
            try:
                result = interp.run(engine_nodes)
            except Exception as e:
                raise MetricsError(f"corpus {digest} vector {i} failed to replay at {level}: {e}") from e
            expected = _decode_vector_value(vector["result"])
            if not _results_match(_float_bits(result), _float_bits(expected)):
                raise MetricsError(f"corpus {digest} vector {i}: result {result!r} != recorded {expected!r} at {level}")
            vec_rows.append({"index": i, "eval_count": interp.eval_count, "dispatch_count": interp.dispatch_count})
            eval_total += interp.eval_count
            dispatch_total += interp.dispatch_count
            vector_total += 1
        rows.append(
            {
                "cfg": digest,
                "static_nodes": stats["static_nodes"],
                "dag_size": stats["dag_size"],
                "vectors": vec_rows,
            }
        )
    rows.sort(key=lambda r: r["cfg"])
    return {
        **_common_metadata(),
        "kind": "rust-corpus",
        "level": level,
        "rust_backend_version": sonolus_backend.backend_version(),
        "entry_count": len(rows),
        "aggregates": {
            "static_nodes": sum(r["static_nodes"] for r in rows),
            "dag_size": sum(r["dag_size"] for r in rows),
            "eval_count": eval_total,
            "dispatch_count": dispatch_total,
            "vector_count": vector_total,
        },
        "rows": rows,
    }


def cmd_corpus(args) -> int:
    current = collect_corpus(args.level)
    agg = current["aggregates"]
    print(
        f"corpus ({args.level}): entries={current['entry_count']} vectors={agg['vector_count']} "
        f"static_nodes={agg['static_nodes']} dag_size={agg['dag_size']} "
        f"eval_count={agg['eval_count']} dispatch_count={agg['dispatch_count']}"
    )
    if args.update or not RUST_CORPUS_FILE.exists():
        _write_json(RUST_CORPUS_FILE, current)
        print(f"wrote {RUST_CORPUS_FILE}")
        return 0
    stored = _load_json(RUST_CORPUS_FILE)
    stored_agg = stored["aggregates"]
    print(
        f"stored ({stored['level']}, commit {stored.get('commit')}): "
        f"eval_count={stored_agg['eval_count']} dispatch_count={stored_agg['dispatch_count']} "
        f"static_nodes={stored_agg['static_nodes']} dag_size={stored_agg['dag_size']}"
    )
    stored_vecs = {(row["cfg"], v["index"]): v for row in stored["rows"] for v in row["vectors"]}
    worse = []
    for row in current["rows"]:
        for v in row["vectors"]:
            old = stored_vecs.get((row["cfg"], v["index"]))
            if old is not None and old["eval_count"] and v["eval_count"] > 1.10 * old["eval_count"]:
                worse.append((v["eval_count"] / old["eval_count"], row["cfg"], v["index"]))
    print(f"vectors >10% worse on eval_count vs stored: {len(worse)}")
    for ratio, digest, index in sorted(worse, reverse=True)[:20]:
        print(f"  {digest} vector {index}: x{ratio:.3f}")
    return 0


# ----------------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_baseline = sub.add_parser("baseline", help="regenerate the frozen-Python baseline files")
    p_baseline.add_argument("--runs", type=int, default=DEFAULT_TIMING_RUNS, help="timing runs per callback (>=5)")
    p_baseline.add_argument(
        "--limit", type=int, default=None, help="dev aid: only the first N callbacks (do not commit)"
    )
    p_baseline.add_argument(
        "--check-determinism",
        action="store_true",
        help="recompute and compare against the stored files with volatile fields stripped; writes nothing",
    )
    p_baseline.add_argument(
        "--stub-runtime-ops",
        action="store_true",
        help="METRICS-only: runtime-only ops (Draw/BeatToTime/...) evaluate their args and produce 0.0 "
        "instead of trapping; recorded in the baseline metadata (see the module docstring)",
    )
    p_baseline.set_defaults(func=cmd_baseline)

    p_report = sub.add_parser("report", help="compare the current Rust backend against the stored baseline")
    p_report.add_argument("--level", default="standard", choices=["minimal", "fast", "standard"])
    p_report.add_argument("--limit", type=int, default=None, help="dev aid: only the first N baseline rows")
    p_report.add_argument("--worst", type=int, default=20, help="how many worst callbacks to list")
    p_report.add_argument("--ratchet", action="store_true", help="exit 1 unless the G3.3 ratchet passes")
    p_report.add_argument(
        "--stub-runtime-ops",
        action="store_true",
        help="METRICS-only: enable the runtime-op stub mode for the Rust dynamic runs; must match the "
        "baseline's recorded mode for a valid comparison (see the module docstring)",
    )
    p_report.set_defaults(func=cmd_report)

    p_corpus = sub.add_parser("corpus", help="Rust-side mini-corpus metrics (compared Rust-vs-Rust across waves)")
    p_corpus.add_argument("--level", default="standard", choices=["minimal", "fast", "standard"])
    p_corpus.add_argument("--update", action="store_true", help="rewrite rust/baselines/rust-corpus.json")
    p_corpus.set_defaults(func=cmd_corpus)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
