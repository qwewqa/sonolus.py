"""Opt-in per-stage compile-time profiling.

Accumulates wall time per named compile stage -- frontend tracing, marshal-in,
each optimizer pass, and emit -- so a build can be broken down and the container
work measured against real numbers.

Enabled by the `SONOLUS_OPT_PROFILE=1` environment variable (read at import,
mirroring `SONOLUS_OPT_TRACE`) or by `enable()` (the CLI `--profile` flag).
Zero cost when disabled: the instrumented hot paths check `enabled` and skip
the timing calls entirely.

The accumulator is process-global; it is never touched from a nogil region (all
recording happens at the GIL-held Python/marshal boundaries), and builds are
serial, so no locking is needed. Call `reset()` before a build to measure just
that build.
"""

from __future__ import annotations

import os
from time import perf_counter_ns

enabled: bool = os.environ.get("SONOLUS_OPT_PROFILE") == "1"

# stage name -> [total_ns, call_count]. Insertion order is preserved for stable
# reporting; it reflects first-touch order, not hash order.
_stages: dict[str, list[int]] = {}


def enable() -> None:
    """Turn profiling on (e.g. from the CLI `--profile` flag)."""
    global enabled  # noqa: PLW0603
    enabled = True


def reset() -> None:
    """Clear all accumulated stage timings."""
    _stages.clear()


def now_ns() -> int:
    """Monotonic wall-clock timestamp in nanoseconds."""
    return perf_counter_ns()


def record(name: str, ns: int) -> None:
    """Add a `ns`-nanosecond sample to stage `name`."""
    entry = _stages.get(name)
    if entry is None:
        _stages[name] = [ns, 1]
    else:
        entry[0] += ns
        entry[1] += 1


def snapshot() -> dict[str, dict[str, int]]:
    """Return `{stage: {"total_ns", "count"}}` for every recorded stage."""
    return {name: {"total_ns": total, "count": count} for name, (total, count) in _stages.items()}


def summary() -> dict:
    """Return the full profile: per-stage totals plus the summed stage time."""
    stages = snapshot()
    total_ns = sum(entry["total_ns"] for entry in stages.values())
    return {"stages": stages, "total_ns": total_ns}


def format_text() -> str:
    """Render a human-readable per-stage table sorted by total time (descending)."""
    stages = snapshot()
    if not stages:
        return "opt profile: no stages recorded"
    ordered = sorted(stages.items(), key=lambda kv: kv[1]["total_ns"], reverse=True)
    total = sum(entry["total_ns"] for _, entry in ordered) or 1
    lines = ["opt profile (per compile stage):", f"  {'stage':<16}{'total':>10}{'share':>8}{'calls':>9}"]
    for name, entry in ordered:
        ms = entry["total_ns"] / 1_000_000
        share = 100 * entry["total_ns"] / total
        lines.append(f"  {name:<16}{ms:>8.1f}ms{share:>7.1f}%{entry['count']:>9}")
    return "\n".join(lines)
