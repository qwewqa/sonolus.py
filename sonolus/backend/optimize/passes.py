from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from sonolus.backend.mode import Mode
from sonolus.backend.optimize.flow import BasicBlock


@dataclass
class OptimizerConfig:
    mode: Mode | None = None
    callback: str | None = None


class CompilerPass(ABC):
    def requires(self) -> set[CompilerPass]:
        return set()

    def preserves(self) -> set[CompilerPass] | None:
        return None

    def destroys(self) -> set[CompilerPass] | None:
        return None

    def applies(self) -> set[CompilerPass]:
        return {self}

    def exists_after(self, passes: set[CompilerPass]) -> set[CompilerPass]:
        preserved = self.preserves()
        destroyed = self.destroys()
        if destroyed is None and preserved is None:
            return self.applies()
        if preserved is not None:
            passes = {p for p in passes if p in preserved}
        if destroyed is not None:
            passes = {p for p in passes if p not in destroyed}
        return passes | self.applies()

    @abstractmethod
    def run(self, entry: BasicBlock, config: OptimizerConfig) -> BasicBlock:
        pass


def run_passes(entry: BasicBlock, passes: Sequence[CompilerPass], config: OptimizerConfig) -> BasicBlock:
    active_passes = set()
    queue = deque(passes)
    while queue:
        if len(queue) > 99:
            raise RuntimeError("Likely unsatisfiable pass requirements")
        current_pass = queue.popleft()
        missing_requirements = current_pass.requires() - active_passes
        if missing_requirements:
            queue.appendleft(current_pass)
            queue.extendleft(missing_requirements)
            continue
        entry = current_pass.run(entry, config)
        active_passes = current_pass.exists_after(active_passes)
    return entry
