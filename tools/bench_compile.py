"""Compile-time benchmark for full pydori engine builds (OPTIMIZER_REWRITE.md §10).

Times ``package_engine(project.engine.data, BuildConfig(passes=...))`` for the ``fast``
and ``standard`` levels, repeating each build and reporting min/median wall seconds.
Intended to run manually and, later, in CI as an informational (non-gating) job.

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


def bench_level(engine, passes, repeat: int) -> list[float]:
    samples: list[float] = []
    for _ in range(repeat):
        config = BuildConfig(passes=passes)
        start = perf_counter()
        package_engine(engine, config)
        samples.append(perf_counter() - start)
    return samples


def run(project_name: str, level_names: list[str], repeat: int) -> dict:
    project = load_project(project_name)
    engine = project.engine.data

    results: dict[str, dict] = {}
    for level_name in level_names:
        samples = bench_level(engine, LEVELS[level_name], repeat)
        results[level_name] = {
            "min_s": min(samples),
            "median_s": median(samples),
            "max_s": max(samples),
            "samples": samples,
        }

    return {
        "meta": {
            "project": project_name,
            "repeat": repeat,
            "levels": list(level_names),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "gil_enabled": bool(getattr(sys, "_is_gil_enabled", lambda: True)()),
            "platform": platform.platform(),
        },
        "results": results,
    }


def print_text(data: dict) -> None:
    meta = data["meta"]
    print(
        f"pydori full-build compile benchmark  "
        f"(project={meta['project']}, repeat={meta['repeat']}, "
        f"python={meta['python_version']}, gil={'on' if meta['gil_enabled'] else 'off'})"
    )
    print(f"{'level':<12}{'min (s)':>12}{'median (s)':>14}{'max (s)':>12}")
    print("-" * 50)
    for level_name, r in data["results"].items():
        print(f"{level_name:<12}{r['min_s']:>12.4f}{r['median_s']:>14.4f}{r['max_s']:>12.4f}")


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
