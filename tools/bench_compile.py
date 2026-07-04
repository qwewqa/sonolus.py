"""Compile-time benchmark for full pydori engine builds.

Times ``package_engine(project.engine.data, BuildConfig(passes=...))`` for the ``fast``
and ``standard`` levels and reports the frontend / optimize+emit split, so the effect of
optimizer changes on each phase stays measurable. Builds are serial.

Standard library only (plus the ``sonolus`` package and the regression project).

Usage::

    uv run python tools/bench_compile.py
    uv run python tools/bench_compile.py --repeat 5 --levels fast,standard
    uv run python tools/bench_compile.py --repeat 1 --json bench.json
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
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
from sonolus.build.engine import package_engine
from sonolus.script.project import BuildConfig

LEVELS = {
    "minimal": BuildConfig.MINIMAL_PASSES,
    "fast": BuildConfig.FAST_PASSES,
    "standard": BuildConfig.STANDARD_PASSES,
}

PROJECTS = {"pydori": ("pydori.project", "project")}


def load_project(name: str):
    if name not in PROJECTS:
        raise SystemExit(f"Unknown project {name!r}; known: {', '.join(sorted(PROJECTS))}")
    module_name, attr = PROJECTS[name]
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _bench_full(engine, passes, repeat: int) -> list[float]:
    """Full-build wall times."""
    samples: list[float] = []
    for _ in range(repeat):
        config = BuildConfig(passes=passes)
        start = perf_counter()
        package_engine(engine, config)
        samples.append(perf_counter() - start)
    return samples


def _phase_split(engine, passes) -> dict:
    """One instrumented build: frontend vs optimize+emit wall time.

    Wraps ``callback_to_cfg`` (frontend tracing) and ``optimize_and_finalize``
    (optimize+emit) to accumulate the time spent in each; the remainder of the
    total is packaging overhead.
    """
    acc = {"frontend": 0.0, "optimize": 0.0}
    orig_cb = _compile_mod.callback_to_cfg
    orig_of = _opt_mod.optimize_and_finalize
    import sonolus.backend._opt.driver as _drv  # noqa: PLC2701 - reset the driver's cached finalize

    def timed_cb(*a, **k):
        t = perf_counter()
        try:
            return orig_cb(*a, **k)
        finally:
            acc["frontend"] += perf_counter() - t

    def timed_of(*a, **k):
        t = perf_counter()
        try:
            return orig_of(*a, **k)
        finally:
            acc["optimize"] += perf_counter() - t

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
        "optimize_emit_fraction": (acc["optimize"] / work) if work else 0.0,
    }


def run(project_name: str, level_names: list[str], repeat: int) -> dict:
    project = load_project(project_name)
    engine = project.engine.data

    results: dict[str, dict] = {}
    for level_name in level_names:
        passes = LEVELS[level_name]
        s = _bench_full(engine, passes, repeat)
        results[level_name] = {
            "min_s": min(s),
            "median_s": median(s),
            "samples": s,
            "split": _phase_split(engine, passes),
        }

    return {
        "meta": {
            "project": project_name,
            "repeat": repeat,
            "levels": list(level_names),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
        },
        "results": results,
    }


def print_text(data: dict) -> None:
    meta = data["meta"]
    print(
        f"pydori full-build compile benchmark  "
        f"(project={meta['project']}, repeat={meta['repeat']}, python={meta['python_version']})"
    )
    print(f"{'level':<10}{'min':>10}{'median':>10}   {'frontend / optimize+emit split'}")
    print("-" * 70)
    for level_name, r in data["results"].items():
        sr = r["split"]
        split = (
            f"frontend {sr['frontend_fraction'] * 100:.0f}% / optimize+emit {sr['optimize_emit_fraction'] * 100:.0f}%"
        )
        print(f"{level_name:<10}{r['min_s']:>9.3f}s{r['median_s']:>9.3f}s   {split}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default="pydori", help="Regression project (default: pydori)")
    parser.add_argument("--levels", default="fast,standard", help="Comma-separated levels (default: fast,standard)")
    parser.add_argument("--repeat", type=int, default=3, help="Builds per level (default: 3)")
    parser.add_argument("--json", dest="json_out", default=None, help="Also write full results as JSON to this path")
    args = parser.parse_args(argv)

    level_names = [lv.strip() for lv in args.levels.split(",") if lv.strip()]
    for lv in level_names:
        if lv not in LEVELS:
            parser.error(f"unknown level {lv!r}; known: {', '.join(LEVELS)}")
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")

    data = run(args.project, level_names, args.repeat)
    print_text(data)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nWrote {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
