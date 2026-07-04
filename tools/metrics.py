"""Node-count and compile-time metrics capture for the optimizer.

This tool compiles every callback of a regression project (default: ``pydori``)
at one or more optimization levels and records, per callback:

* ``function_node_count`` / ``value_node_count`` -- emitted ``EngineNode`` counts,
  counted **per reference** over the expanded node tree (shared/hash-consed nodes
  are re-executed per reference by the real runtime).
* ``effective_node_count`` -- the same tree, but every *maximal* runtime-constant
  subtree counts as 1 (models the runtime's own constant folding). This is the number
  the gate test (``tests/regressions/test_metrics_gate.py``) compares against.
  Runtime-constant follows the optimizer's runtime-constant rule (constant-index reads
  of ``RUNTIME_CONSTANT_BLOCKS`` that are *not writable in the current callback*), and
  the CFG-skeleton nodes (Block/JumpLoop/Execute/terminators) never fold -- the runtime
  compiles block structure to bytecode and folds only the expressions within.
* ``per_op_counts`` -- expanded, per-reference op-name -> count.
* ``timing`` -- wall-clock split into frontend tracing / optimize / emit.

It also implements a warm dev-server rebuild timing mode (``--dev-rebuild``).

Standard library only (plus the ``sonolus`` package and the regression project).

Usage::

    uv run python tools/metrics.py                      # print JSON to stdout
    uv run python tools/metrics.py --output baseline.json
    uv run python tools/metrics.py --levels fast,standard --repeat 3
    uv run python tools/metrics.py --limit 5            # small subset (validation)
    uv run python tools/metrics.py --dev-rebuild
    uv run python tools/metrics.py --full-baseline --repeat 3 --output baseline.json
    # ^ one JSON: callbacks+totals, dev_rebuild (repeat 3), full_build timings (repeat 3)
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

# --- sys.path: make <repo> and <repo>/test_projects importable when run from repo root ---
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT / "test_projects"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Frontend tracing can recurse; match the limit the CLI uses for builds (sonolus/build/cli.py).
# Node/CFG *analysis* in this module is iterative, so it does not depend on this.
sys.setrecursionlimit(10_000)

# Blocks whose constant-index read the real runtime constant-folds. Imported from
# the optimizer core so there is a single source of truth, not a drifting duplicate.
from sonolus.backend._opt.ir import RUNTIME_CONSTANT_BLOCKS  # noqa: PLC2701

from sonolus.backend.mode import Mode
from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.build.compile import callback_to_cfg
from sonolus.script.internal.callbacks import (
    navigate_callback,
    preprocess_callback,
    update_callback,
    update_spawn_callback,
)
from sonolus.script.internal.context import (
    ModeContextState,
    ProjectContextState,
    RuntimeChecks,
)
from sonolus.script.project import BuildConfig

LEVELS: dict[str, object] = {
    "minimal": BuildConfig.MINIMAL_PASSES,
    "fast": BuildConfig.FAST_PASSES,
    "standard": BuildConfig.STANDARD_PASSES,
}


def camel_to_snake(name: str) -> str:
    """Mirror tests/regressions/test_project.py:camel_to_snake for stable keys."""
    return "".join(f"_{c.lower()}" if c.isupper() else c for c in name).lstrip("_")


# --------------------------------------------------------------------------------------
# Project / callback enumeration (mirrors tests/regressions/test_project.py exactly)
# --------------------------------------------------------------------------------------

PROJECTS = {"pydori": ("pydori.project", "project")}


def load_project(name: str):
    if name not in PROJECTS:
        raise SystemExit(f"Unknown project {name!r}; known: {', '.join(sorted(PROJECTS))}")
    module_name, attr = PROJECTS[name]
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, attr)


class CallbackTask:
    """A single (mode, callback) unit of work with a stable metrics key."""

    __slots__ = ("archetype", "archetypes_map", "callback_name", "cb", "cb_name", "key", "mode")

    def __init__(self, mode, archetype, cb, cb_name, callback_name, archetypes_map, key):
        self.mode = mode
        self.archetype = archetype
        self.cb = cb
        self.cb_name = cb_name
        self.callback_name = callback_name
        self.archetypes_map = archetypes_map
        self.key = key


def _mode_tasks(project_name, mode, archetypes, global_callbacks):
    """Enumerate callbacks for one mode, mirroring _build_mode_callbacks (non-dev)."""
    archetypes_map = {a: i for i, a in enumerate(archetypes)} if archetypes is not None else None
    mode_label = mode.name.lower()

    for archetype in archetypes or []:
        archetype._init_fields()
        callback_items = [
            (cb_name, cb_info, getattr(archetype, cb_name))
            for cb_name, cb_info in archetype._supported_callbacks_.items()
            if getattr(archetype, cb_name) not in archetype._default_callbacks_
        ]
        for cb_name, cb_info, cb in callback_items:
            key = f"{project_name}_{mode_label}_{camel_to_snake(archetype.__name__)}_{cb_name}"
            yield CallbackTask(mode, archetype, cb, cb_name, cb_info.name, archetypes_map, key)

    for cb_info, cb in global_callbacks or []:
        key = f"{project_name}_{mode_label}_global_{camel_to_snake(cb_info.name)}"
        yield CallbackTask(mode, None, cb, cb_info.name, cb_info.name, archetypes_map, key)


def iter_callback_tasks(project_name, project):
    engine = project.engine.data
    yield from _mode_tasks(project_name, Mode.PLAY, engine.play.archetypes, None)
    yield from _mode_tasks(
        project_name,
        Mode.WATCH,
        engine.watch.archetypes,
        [(update_spawn_callback, engine.watch.update_spawn)],
    )
    yield from _mode_tasks(project_name, Mode.PREVIEW, engine.preview.archetypes, None)
    yield from _mode_tasks(
        project_name,
        Mode.TUTORIAL,
        None,
        [
            (preprocess_callback, engine.tutorial.preprocess),
            (navigate_callback, engine.tutorial.navigate),
            (update_callback, engine.tutorial.update),
        ],
    )


# --------------------------------------------------------------------------------------
# Runtime-constant classification + node counting over the emitted tree
# --------------------------------------------------------------------------------------


def _is_value_node(node) -> bool:
    return isinstance(node, (int, float)) and not isinstance(node, bool)


def _block_is_runtime_constant(block, mode, callback_name: str) -> bool:
    """The optimizer's runtime-constant block rule.

    The block id must resolve to a BlockData whose name is in RUNTIME_CONSTANT_BLOCKS
    AND that is not writable in the current callback (a block the callback can write is
    not constant at runtime even if the runtime treats it as constant elsewhere).
    """
    if isinstance(block, float):
        if not block.is_integer():
            return False
        block = int(block)
    if not isinstance(block, int) or isinstance(block, bool):
        return False
    try:
        block_data = mode.blocks(block)
    except ValueError:
        return False
    return block_data.name in RUNTIME_CONSTANT_BLOCKS and callback_name not in block_data.writable


def _node_is_runtime_constant(node: FunctionNode, is_rc: dict[int, bool], mode, callback_name: str) -> bool:
    op = node.func
    if op is Op.Get and len(node.args) == 2:
        block, index = node.args
        return (
            _is_value_node(block) and _is_value_node(index) and _block_is_runtime_constant(block, mode, callback_name)
        )
    if op.pure:
        # Pure ops (including And/Or/If/Switch appearing *inside* statement trees) fold
        # when all args are runtime-constant. Classification is value-based; the CFG
        # skeleton is handled positionally by _skeleton_effective, not by excluding
        # nodes here, so a terminator that the emitter interned to the same object as a
        # statement select still folds correctly at its statement occurrence.
        return all(is_rc[id(arg)] for arg in node.args)
    return False


def _skeleton_effective(root, sub_eff: dict[int, int]) -> int:
    """Effective node count with the CFG skeleton classified by *position*.

    The runtime compiles the block *structure* to bytecode and constant-folds only the
    expressions within, so the outer Block, the JumpLoop, each per-block Execute, and
    each Execute's final terminator (If/Switch* over block indexes) are structural
    containers that never fold: each counts 1, and its operand subtrees (statements,
    terminator test expressions) contribute their value-based ``sub_eff``.

    Position-based, not identity-based: the hash-consing emitter can intern a terminator
    node to the very same object as a statement-level select, so an identity set would
    wrongly force that shared statement subtree to never fold (inflating the count).
    Walking the Block -> JumpLoop -> Execute* -> terminator spine by position keeps each
    occurrence classified by where it sits in the tree.
    """
    if not (isinstance(root, FunctionNode) and root.func is Op.Block and len(root.args) == 1):
        return sub_eff[id(root)]
    jump_loop = root.args[0]
    if not (isinstance(jump_loop, FunctionNode) and jump_loop.func is Op.JumpLoop):
        return sub_eff[id(root)]
    total = 2  # Block + JumpLoop container nodes
    for execute in jump_loop.args:
        if not (isinstance(execute, FunctionNode) and execute.func is Op.Execute):
            total += sub_eff[id(execute)]
            continue
        total += 1  # Execute container node
        args = execute.args
        for stmt in args[:-1]:
            total += sub_eff[id(stmt)]
        if args:
            terminator = args[-1]
            if isinstance(terminator, FunctionNode):
                total += 1  # terminator container node
                for targ in terminator.args:
                    total += sub_eff[id(targ)]
            else:
                total += sub_eff[id(terminator)]
    return total


def _post_order(root):
    """Return unique nodes (by identity) in topological post-order.

    Every node appears after all of its descendants. Works for trees and DAGs
    (the emitted node tree is hash-consed, so shared subtrees are DAG edges);
    each unique node appears exactly once. The tree is acyclic by construction.
    """
    order = []
    entered: set[int] = set()
    done: set[int] = set()
    stack = [(root, False)]
    while stack:
        node, processed = stack.pop()
        nid = id(node)
        if processed:
            if nid in done:
                continue
            done.add(nid)
            order.append(node)
            continue
        if nid in entered:
            continue
        entered.add(nid)
        stack.append((node, True))
        if isinstance(node, FunctionNode):
            stack.extend((arg, False) for arg in node.args if id(arg) not in entered)
    return order


def analyze_node(root, mode, callback_name: str) -> dict:
    """Compute per-reference node counts, effective count, and per-op counts.

    Counts are memoized per unique node and combined so shared subtrees are
    counted once per reference without exponential expansion.
    """
    post = _post_order(root)

    is_rc: dict[int, bool] = {}
    sub_fn: dict[int, int] = {}
    sub_val: dict[int, int] = {}
    sub_eff: dict[int, int] = {}
    sub_ops: dict[int, Counter] = {}

    for node in post:
        nid = id(node)
        if isinstance(node, FunctionNode):
            is_rc[nid] = _node_is_runtime_constant(node, is_rc, mode, callback_name)
            fn = 1
            val = 0
            ops: Counter = Counter()
            ops[node.func.name] += 1
            for arg in node.args:
                aid = id(arg)
                fn += sub_fn[aid]
                val += sub_val[aid]
                ops += sub_ops[aid]
            sub_fn[nid] = fn
            sub_val[nid] = val
            sub_ops[nid] = ops
            if is_rc[nid]:
                sub_eff[nid] = 1
            else:
                sub_eff[nid] = 1 + sum(sub_eff[id(arg)] for arg in node.args)
        else:  # value node (int/float): always runtime-constant
            is_rc[nid] = True
            sub_fn[nid] = 0
            sub_val[nid] = 1
            sub_eff[nid] = 1
            sub_ops[nid] = Counter()

    rid = id(root)
    return {
        "function_node_count": sub_fn[rid],
        "value_node_count": sub_val[rid],
        "effective_node_count": _skeleton_effective(root, sub_eff),
        "per_op_counts": dict(sub_ops[rid]),
    }


# --------------------------------------------------------------------------------------
# Per-callback measurement
# --------------------------------------------------------------------------------------


def measure_callback(project_name, task: CallbackTask, level_name, passes, repeat) -> dict:
    frontend_times: list[float] = []
    optimize_times: list[float] = []
    emit_times: list[float] = []
    reference: dict | None = None

    for _ in range(repeat):
        project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
        mode_state = ModeContextState(task.mode, task.archetypes_map)

        t0 = perf_counter()
        cfg = callback_to_cfg(project_state, mode_state, task.cb, task.callback_name, task.archetype)
        t1 = perf_counter()
        cfg = run_passes(cfg, passes, OptimizerConfig(mode=task.mode, callback=task.callback_name))
        t2 = perf_counter()
        node = cfg_to_engine_node(cfg)
        t3 = perf_counter()

        counts = analyze_node(node, task.mode, task.callback_name)
        # Invariant self-checks (tool correctness).
        assert counts["effective_node_count"] <= counts["function_node_count"] + counts["value_node_count"], (
            f"effective > function+value for {task.key} [{level_name}]"
        )
        assert sum(counts["per_op_counts"].values()) == counts["function_node_count"], (
            f"per_op total != function count for {task.key} [{level_name}]"
        )

        snapshot = dict(counts)
        if reference is None:
            reference = snapshot
        elif snapshot != reference:
            raise AssertionError(
                f"Non-deterministic counts across repeats for {task.key} [{level_name}]:\n"
                f"  first={reference}\n  now={snapshot}"
            )

        frontend_times.append(t1 - t0)
        optimize_times.append(t2 - t1)
        emit_times.append(t3 - t2)

    assert reference is not None
    return {
        **reference,
        "timing": {
            "frontend_s": min(frontend_times),
            "optimize_s": min(optimize_times),
            "emit_s": min(emit_times),
        },
    }


def _empty_totals() -> dict:
    return {
        "callback_count": 0,
        "function_node_count": 0,
        "value_node_count": 0,
        "effective_node_count": 0,
        "per_op_counts": Counter(),
        "timing": {"frontend_s": 0.0, "optimize_s": 0.0, "emit_s": 0.0},
    }


def _accumulate(totals: dict, result: dict) -> None:
    totals["callback_count"] += 1
    totals["function_node_count"] += result["function_node_count"]
    totals["value_node_count"] += result["value_node_count"]
    totals["effective_node_count"] += result["effective_node_count"]
    totals["per_op_counts"] += Counter(result["per_op_counts"])
    for phase in ("frontend_s", "optimize_s", "emit_s"):
        totals["timing"][phase] += result["timing"][phase]


def _finalize_totals(totals: dict) -> dict:
    out = dict(totals)
    out["per_op_counts"] = dict(totals["per_op_counts"])
    return out


def run_metrics(project_name, level_names, repeat, limit) -> dict:
    project = load_project(project_name)
    tasks = list(iter_callback_tasks(project_name, project))
    if limit is not None:
        tasks = tasks[:limit]

    levels_out: dict[str, dict] = {}
    grand = _empty_totals()

    for level_name in level_names:
        passes = LEVELS[level_name]
        callbacks: dict[str, dict] = {}
        level_totals = _empty_totals()
        for task in tasks:
            result = measure_callback(project_name, task, level_name, passes, repeat)
            callbacks[task.key] = result
            _accumulate(level_totals, result)
            _accumulate(grand, result)
        levels_out[level_name] = {
            "callbacks": callbacks,
            "totals": _finalize_totals(level_totals),
        }

    return {
        "meta": _build_meta(project_name, level_names, repeat, limit),
        "levels": levels_out,
        "grand_totals": _finalize_totals(grand),
    }


# --------------------------------------------------------------------------------------
# Warm dev-server rebuild timing
# --------------------------------------------------------------------------------------


def _dev_rebuild_via_collection(project, config, repeat):
    """Faithful replication of a warm dev rebuild.

    build_collection cold, then time repeated warm rebuilds via
    build_project_to_existing_collection (dev_server.py:110-155). Requires the project's
    resources to resolve (skins/particles/etc.).
    """
    from sonolus.build.cli import build_collection
    from sonolus.build.project import build_project_to_existing_collection

    with tempfile.TemporaryDirectory(prefix="sonolus-devrebuild-") as tmp:
        project_state = ProjectContextState.from_build_config(config)

        cold_start = perf_counter()
        collection = build_collection(project, Path(tmp), config, project_state=project_state)
        cold_seconds = perf_counter() - cold_start

        warm_seconds: list[float] = []
        for _ in range(repeat):
            # RebuildCommand creates a fresh project_state each rebuild. Compilation is
            # cache-free, so a "warm" rebuild re-optimizes every callback.
            rebuild_state = ProjectContextState.from_build_config(config)
            start = perf_counter()
            build_project_to_existing_collection(project, collection, config, project_state=rebuild_state)
            warm_seconds.append(perf_counter() - start)

    return "build_project_to_existing_collection", cold_seconds, warm_seconds


def _dev_rebuild_via_package_engine(project, config, repeat):
    """Fallback for projects without resolvable resources (e.g. pydori ships none).

    Drives package_engine directly -- the exact compile step add_engine_to_collection
    invokes. Compilation is cache-free, so a "warm" rebuild re-optimizes every callback.
    """
    from sonolus.build.engine import package_engine

    engine = project.engine.data
    project_state = ProjectContextState.from_build_config(config)

    cold_start = perf_counter()
    package_engine(engine, config, project_state=project_state)
    cold_seconds = perf_counter() - cold_start

    warm_seconds: list[float] = []
    for _ in range(repeat):
        rebuild_state = ProjectContextState.from_build_config(config)
        start = perf_counter()
        package_engine(engine, config, project_state=rebuild_state)
        warm_seconds.append(perf_counter() - start)

    return "package_engine", cold_seconds, warm_seconds


def run_dev_rebuild(project_name, level_name, repeat) -> dict:
    """Warm dev-server rebuild timing.

    Replicates dev_server.RebuildCommand.execute for a warm rebuild, minus the HTTP
    server and the sys.modules purge / project re-import. Module-reload cost is excluded.
    """
    payload = _dev_rebuild_section(project_name, level_name, repeat)
    return {
        "meta": {
            **_build_meta(project_name, [level_name], repeat, None),
            "mode": "dev_rebuild_warm_cache",
            **payload["config"],
        },
        "cold_build_s": payload["cold_build_s"],
        "warm_rebuild_s": payload["warm_rebuild_s"],
    }


def _dev_rebuild_section(project_name, level_name, repeat) -> dict:
    """Compute the dev-rebuild timings as an embeddable JSON section (no global meta)."""
    from statistics import median

    from sonolus.script.project import BuildConfig as _BuildConfig

    project = load_project(project_name)
    # Runtime checks mirror the dev CLI (notify-and-terminate). Passes come from
    # --dev-rebuild-level; the default stays "fast" for comparability with the v0.16
    # baseline even though the dev CLI itself now defaults to standard (-O2).
    config = _BuildConfig(passes=LEVELS[level_name], runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE)

    notes = [
        "Dev-server rebuild timing (compilation is cache-free; every rebuild re-optimizes all callbacks).",
        "sys.modules purge + project re-import cost is EXCLUDED (module-reload cost not modeled).",
    ]
    try:
        method, cold_seconds, warm_seconds = _dev_rebuild_via_collection(project, config, repeat)
        notes.append("Timed region = build_project_to_existing_collection (cache-free re-optimize).")
        notes.append("write_collection is outside the timed region.")
    except Exception as exc:
        method, cold_seconds, warm_seconds = _dev_rebuild_via_package_engine(project, config, repeat)
        notes.append(
            "Full collection build unavailable "
            f"({type(exc).__name__}: {exc}); measured package_engine directly instead "
            "(the compile step add_engine_to_collection invokes)."
        )
        notes.append("Timed region = package_engine (cache-free re-optimize).")

    return {
        "config": {
            "level": level_name,
            "repeat": repeat,
            "runtime_checks": config.runtime_checks.value,
            "timed_call": method,
            "notes": notes,
        },
        "cold_build_s": cold_seconds,
        "warm_rebuild_s": {
            "min": min(warm_seconds),
            "median": median(warm_seconds),
            "samples": warm_seconds,
        },
    }


# --------------------------------------------------------------------------------------
# Full-build timing + combined baseline capture
# --------------------------------------------------------------------------------------


def run_full_build(project_name, level_names, repeat) -> dict:
    """Time full package_engine builds per level (bench_compile-style, embeddable)."""
    from statistics import median

    from sonolus.build.engine import package_engine

    project = load_project(project_name)
    engine = project.engine.data

    levels: dict[str, dict] = {}
    for level_name in level_names:
        samples: list[float] = []
        for _ in range(repeat):
            config = BuildConfig(passes=LEVELS[level_name])
            start = perf_counter()
            package_engine(engine, config)
            samples.append(perf_counter() - start)
        levels[level_name] = {
            "min_s": min(samples),
            "median_s": median(samples),
            "samples": samples,
        }

    return {"repeat": repeat, "timed_call": "package_engine", "levels": levels}


def run_full_baseline(
    project_name,
    level_names,
    repeat,
    limit,
    dev_rebuild_level,
    dev_rebuild_repeat=3,
    full_build_repeat=3,
) -> dict:
    """Produce ONE JSON with per-callback metrics, dev-rebuild timing, and full-build timings."""
    data = run_metrics(project_name, level_names, repeat, limit)
    data["meta"]["mode"] = "full_baseline"
    data["dev_rebuild"] = _dev_rebuild_section(project_name, dev_rebuild_level, dev_rebuild_repeat)
    data["full_build"] = run_full_build(project_name, level_names, full_build_repeat)
    return data


# --------------------------------------------------------------------------------------
# Meta / output
# --------------------------------------------------------------------------------------


def _repo_version() -> str | None:
    pyproject = _REPO_ROOT / "pyproject.toml"
    try:
        import tomllib

        with pyproject.open("rb") as f:
            return tomllib.load(f)["project"]["version"]
    except Exception:
        return None


def _git_rev() -> str | None:
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if rev.returncode == 0:
            return rev.stdout.strip()
    except Exception:
        pass
    return None


def _build_meta(project_name, level_names, repeat, limit) -> dict:
    return {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "gil_enabled": bool(getattr(sys, "_is_gil_enabled", lambda: True)()),
        "platform": platform.platform(),
        "repo_version": _repo_version(),
        "git_rev": _git_rev(),
        "project": project_name,
        "levels": list(level_names),
        "repeat": repeat,
        "limit": limit,
        "counting": (
            "per-reference over expanded EngineNode tree; effective = maximal runtime-constant subtree counts as 1; "
            "runtime-constant = constant-index read of RUNTIME_CONSTANT_BLOCKS not writable in the callback; "
            "CFG skeleton nodes (Block/JumpLoop/Execute/terminators) never fold"
        ),
    }


def _dump(data: dict, output: str | None) -> None:
    text = json.dumps(data, indent=2, sort_keys=True)
    if output is None:
        print(text)
    else:
        Path(output).write_text(text, encoding="utf-8")
        print(f"Wrote {output}", file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default="pydori", help="Regression project (default: pydori)")
    parser.add_argument(
        "--levels",
        default="fast,standard",
        help="Comma-separated optimization levels (minimal,fast,standard). Default: fast,standard",
    )
    parser.add_argument(
        "--repeat", type=int, default=1, help="Repeat timing runs; min per phase is kept (counts asserted identical)"
    )
    parser.add_argument("--limit", type=int, default=None, help="Only measure the first N callbacks (validation)")
    parser.add_argument("--output", default=None, help="Write JSON to this path (default: stdout)")
    parser.add_argument(
        "--dev-rebuild", action="store_true", help="Run warm-cache dev-server rebuild timing instead of node metrics"
    )
    parser.add_argument(
        "--dev-rebuild-level", default="fast", help="Optimization level for the dev-rebuild timing (default: fast)"
    )
    parser.add_argument(
        "--full-baseline",
        action="store_true",
        help="Single combined capture: per-callback metrics (--repeat) + dev-rebuild timing (repeat 3) + package_engine full-build timings (repeat 3), in one JSON",
    )
    args = parser.parse_args(argv)

    if args.dev_rebuild_level not in LEVELS:
        parser.error(f"unknown --dev-rebuild-level {args.dev_rebuild_level!r}")

    if args.dev_rebuild:
        repeat = args.repeat if args.repeat and args.repeat > 1 else 3
        data = run_dev_rebuild(args.project, args.dev_rebuild_level, repeat)
        _dump(data, args.output)
        return 0

    level_names = [lv.strip() for lv in args.levels.split(",") if lv.strip()]
    for lv in level_names:
        if lv not in LEVELS:
            parser.error(f"unknown level {lv!r}; known: {', '.join(LEVELS)}")
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")

    if args.full_baseline:
        data = run_full_baseline(args.project, level_names, args.repeat, args.limit, args.dev_rebuild_level)
    else:
        data = run_metrics(args.project, level_names, args.repeat, args.limit)
    _dump(data, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
