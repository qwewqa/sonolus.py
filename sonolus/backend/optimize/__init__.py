"""Public optimizer API -- a thin Python shim over the compiled `_opt` core.

Exposes opaque optimization-level sentinels and three entry points. The heavy
lifting lives in the Cython `sonolus.backend._opt` package
(marshal in -> passes (nogil) -> export back | emit).
"""

from __future__ import annotations

from dataclasses import dataclass

from sonolus.backend.mode import Mode
from sonolus.backend.node import EngineNode
from sonolus.backend.optimize.flow import BasicBlock

# NOTE: the compiled `_opt` modules (`driver`/`emit`) are imported lazily
# inside the functions below, not at module top. `_opt.ir` imports
# `sonolus.backend.optimize.flow`, which requires *this* package to initialize
# first -- importing `_opt.driver` here would form an import cycle
# (optimize -> _opt.driver -> _opt.ir -> optimize.flow -> optimize).


class OptimizationLevel:
    """An opaque optimization-level sentinel.

    Identity-compared, unordered, and repr-friendly. There is deliberately no
    comparison protocol -- levels are labels, not a numeric scale.
    """

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return f"OptimizationLevel({self.name!r})"


MINIMAL_PASSES = OptimizationLevel("minimal")  # -O0
FAST_PASSES = OptimizationLevel("fast")  # -O1
STANDARD_PASSES = OptimizationLevel("standard")  # -O2

_LEVEL_NAMES = {
    MINIMAL_PASSES: "minimal",
    FAST_PASSES: "fast",
    STANDARD_PASSES: "standard",
}


@dataclass
class OptimizerConfig:
    """Marshal-in context: the target mode and callback for writability resolution."""

    mode: Mode | None = None
    callback: str | None = None


def _level_name(level: OptimizationLevel) -> str:
    name = _LEVEL_NAMES.get(level)
    if name is None:
        raise ValueError(
            f"Unknown optimization level {level!r} (expected MINIMAL_PASSES, FAST_PASSES, or STANDARD_PASSES)"
        )
    return name


def run_passes(
    entry: BasicBlock,
    level: OptimizationLevel,
    config: OptimizerConfig | None = None,
    *,
    allocate: bool = True,
) -> BasicBlock:
    """Optimize `entry` at `level` and export a fresh `BasicBlock` CFG.

    Non-destructive on `entry`. `allocate=False` skips temp allocation
    (leaving unallocated temp places), which `visualize_cfg` uses.
    """
    from sonolus.backend._opt import driver

    config = config or OptimizerConfig()
    return driver.run_pipeline_cfg(entry, _level_name(level), config.mode, config.callback, allocate)


def optimize_and_finalize(
    entry: BasicBlock,
    level: OptimizationLevel,
    config: OptimizerConfig | None = None,
) -> EngineNode:
    """Optimize `entry` at `level` and emit its `EngineNode` in one fused pass.

    Equivalent to `cfg_to_engine_node(run_passes(entry, level, config))` but
    without the intermediate `BasicBlock` export/re-import.
    """
    from sonolus.backend._opt import driver

    config = config or OptimizerConfig()
    return driver.optimize_and_finalize_cfg(entry, _level_name(level), config.mode, config.callback)


def cfg_to_engine_node(entry: BasicBlock) -> EngineNode:
    """Emit an `EngineNode` from an already-optimized CFG (no passes run).

    Non-destructive on `entry`. Used by conftest/goldens to emit a CFG that
    `run_passes` already optimized.
    """
    from sonolus.backend._opt import emit

    return emit.emit_cfg(entry)
