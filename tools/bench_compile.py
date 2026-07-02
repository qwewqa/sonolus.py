"""Compile-time benchmark for full pydori engine builds.

Times ``package_engine(project.engine.data, BuildConfig(passes=...))`` for the ``fast``
and ``standard`` levels, single- vs multi-threaded, and reports the frontend / optimize
/ emit split so the parallelism payoff stays measurable: only optimize+emit runs in the
per-callback thread pool (with the Cython optimizer's nogil regions releasing the GIL);
the frontend tracer is serial Python, and if it dominates wall time the pool gains little.

Threading toggle (``--threads``):

* ``compare`` (default) -- run both serial and pooled, report the speedup.
* ``0`` / ``off``       -- pool off (serial): every callback's optimize runs inline.
* ``auto`` / ``on``     -- pool on with the production worker formula.
* ``<N>``               -- pool on with exactly N workers.

Serial mode is realized by swapping ``ThreadPoolExecutor`` for an inline executor
(``submit`` runs synchronously), so the exact same code path is exercised with no threads.

Standard library only (plus the ``sonolus`` package and the regression project).

Usage::

    uv run python tools/bench_compile.py
    uv run python tools/bench_compile.py --repeat 5 --levels fast,standard
    uv run python tools/bench_compile.py --threads 0        # serial only
    uv run python tools/bench_compile.py --threads auto      # pooled only
    uv run python tools/bench_compile.py --repeat 1 --json bench.json
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import threading
from concurrent.futures import Future
from concurrent.futures.thread import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from time import perf_counter

# --- sys.path: make <repo> and <repo>/test_projects importable when run from repo root ---
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT / "test_projects"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

sys.setrecursionlimit(10_000)

import sonolus.backend.optimize as _opt_mod
import sonolus.build.compile as _compile_mod
import sonolus.build.engine as _engine_mod
from sonolus.build.engine import package_engine
from sonolus.script.project import BuildConfig

LEVELS = {
    "minimal": BuildConfig.MINIMAL_PASSES,
    "fast": BuildConfig.FAST_PASSES,
    "standard": BuildConfig.STANDARD_PASSES,
}

PROJECTS = {"pydori": ("pydori.project", "project")}


class _InlineExecutor:
    """Executor stand-in whose ``submit`` runs the task synchronously.

    Lets the benchmark exercise the real ``package_engine`` code path with the pool
    logically "on" but no actual threads, giving a fair serial baseline.
    """

    def submit(self, fn, /, *args, **kwargs):
        f: Future = Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # propagate exactly like a pool worker would
            f.set_exception(exc)
        return f

    def shutdown(self, wait=True, *, cancel_futures=False):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _pool_factory(threads: str):
    """Return a ThreadPoolExecutor factory for the given ``--threads`` value.

    Returns None to leave the production default in place ('auto').
    """
    if threads in {"0", "off"}:
        return lambda *a, **k: _InlineExecutor()
    if threads in {"auto", "on"}:
        return None
    n = int(threads)
    return lambda *a, **k: ThreadPoolExecutor(n)


def load_project(name: str):
    if name not in PROJECTS:
        raise SystemExit(f"Unknown project {name!r}; known: {', '.join(sorted(PROJECTS))}")
    module_name, attr = PROJECTS[name]
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _bench_full(engine, passes, repeat: int, factory) -> list[float]:
    """Full-build wall times, with the pool factory optionally overridden."""
    orig = _engine_mod.ThreadPoolExecutor
    if factory is not None:
        _engine_mod.ThreadPoolExecutor = factory
    try:
        samples: list[float] = []
        for _ in range(repeat):
            config = BuildConfig(passes=passes)
            start = perf_counter()
            package_engine(engine, config)
            samples.append(perf_counter() - start)
        return samples
    finally:
        _engine_mod.ThreadPoolExecutor = orig


def _phase_split(engine, passes) -> dict:
    """One instrumented build: sum of frontend vs optimize+emit wall time.

    frontend is serial (its sum == its wall time); optimize+emit is the sum across
    pool workers (its wall time is less by the pool speedup). ``parallelizable`` is the
    optimize+emit fraction of the summed work -- the ceiling the pool can shrink.
    """
    acc = {"frontend": 0.0, "optimize": 0.0}
    lock = threading.Lock()
    orig_cb = _compile_mod.callback_to_cfg
    orig_of = _opt_mod.optimize_and_finalize
    import sonolus.backend._opt.driver as _drv  # noqa: PLC2701 - reset the driver's cached finalize

    def timed_cb(*a, **k):
        t = perf_counter()
        try:
            return orig_cb(*a, **k)
        finally:
            dt = perf_counter() - t
            with lock:
                acc["frontend"] += dt

    def timed_of(*a, **k):
        t = perf_counter()
        try:
            return orig_of(*a, **k)
        finally:
            dt = perf_counter() - t
            with lock:
                acc["optimize"] += dt

    _compile_mod.callback_to_cfg = timed_cb
    _opt_mod.optimize_and_finalize = timed_of
    _drv._OPT_FINALIZE = timed_of  # driver caches the finalize callable
    try:
        start = perf_counter()
        package_engine(engine, BuildConfig(passes=passes))
        total = perf_counter() - start
    finally:
        _compile_mod.callback_to_cfg = orig_cb
        _opt_mod.optimize_and_finalize = orig_of
        _drv._OPT_FINALIZE = orig_of

    work = acc["frontend"] + acc["optimize"]
    return {
        "total_wall_s": total,
        "frontend_sum_s": acc["frontend"],
        "optimize_emit_sum_s": acc["optimize"],
        "frontend_fraction": (acc["frontend"] / work) if work else 0.0,
        "parallelizable_fraction": (acc["optimize"] / work) if work else 0.0,
    }


def run(project_name: str, level_names: list[str], repeat: int, threads: str) -> dict:
    project = load_project(project_name)
    engine = project.engine.data

    run_serial = threads in {"compare", "0", "off"}
    run_pool = threads not in {"0", "off"}
    pool_factory = None if threads in {"compare", "auto", "on"} else _pool_factory(threads)

    results: dict[str, dict] = {}
    for level_name in level_names:
        passes = LEVELS[level_name]
        entry: dict = {}

        if run_serial:
            s = _bench_full(engine, passes, repeat, _pool_factory("0"))
            entry["serial"] = {"min_s": min(s), "median_s": median(s), "samples": s}
        if run_pool:
            p = _bench_full(engine, passes, repeat, pool_factory)
            entry["pooled"] = {"min_s": min(p), "median_s": median(p), "samples": p}
        if "serial" in entry and "pooled" in entry:
            entry["speedup_min"] = entry["serial"]["min_s"] / entry["pooled"]["min_s"]

        entry["split"] = _phase_split(engine, passes)
        results[level_name] = entry

    return {
        "meta": {
            "project": project_name,
            "repeat": repeat,
            "threads": threads,
            "levels": list(level_names),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "gil_enabled": bool(getattr(sys, "_is_gil_enabled", lambda: True)()),
            "platform": platform.platform(),
            "note": (
                "Only optimize+emit runs in the pool (nogil pass regions); the frontend "
                "tracer is serial Python. 'parallelizable_fraction' is the optimize+emit "
                "share of summed work -- the ceiling on full-build speedup."
            ),
        },
        "results": results,
    }


def print_text(data: dict) -> None:
    meta = data["meta"]
    print(
        f"pydori full-build compile benchmark  "
        f"(project={meta['project']}, repeat={meta['repeat']}, threads={meta['threads']}, "
        f"python={meta['python_version']}, gil={'on' if meta['gil_enabled'] else 'off'})"
    )
    print(f"{'level':<10}{'serial min':>12}{'pooled min':>12}{'speedup':>9}   {'front/opt/emit split'}")
    print("-" * 78)
    for level_name, r in data["results"].items():
        s = f"{r['serial']['min_s']:.3f}s" if "serial" in r else "-"
        p = f"{r['pooled']['min_s']:.3f}s" if "pooled" in r else "-"
        sp = f"{r['speedup_min']:.2f}x" if "speedup_min" in r else "-"
        sr = r["split"]
        split = (
            f"frontend {sr['frontend_fraction'] * 100:.0f}% / "
            f"optimize+emit {sr['parallelizable_fraction'] * 100:.0f}% (parallelizable)"
        )
        print(f"{level_name:<10}{s:>12}{p:>12}{sp:>9}   {split}")
    print()
    print(
        "Note: full-build speedup is bounded by the serial frontend fraction. The optimize+emit\n"
        "phase parallelism is larger in isolation (see the split); measure it with --threads compare."
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default="pydori", help="Regression project (default: pydori)")
    parser.add_argument("--levels", default="fast,standard", help="Comma-separated levels (default: fast,standard)")
    parser.add_argument("--repeat", type=int, default=3, help="Builds per level per mode (default: 3)")
    parser.add_argument(
        "--threads",
        default="compare",
        help="compare (default: serial vs pooled) | 0/off (serial) | auto/on (pooled) | <N> workers",
    )
    parser.add_argument("--json", dest="json_out", default=None, help="Also write full results as JSON to this path")
    args = parser.parse_args(argv)

    level_names = [lv.strip() for lv in args.levels.split(",") if lv.strip()]
    for lv in level_names:
        if lv not in LEVELS:
            parser.error(f"unknown level {lv!r}; known: {', '.join(LEVELS)}")
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")

    data = run(args.project, level_names, args.repeat, args.threads)
    print_text(data)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nWrote {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
