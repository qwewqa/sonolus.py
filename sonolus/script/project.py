from os import PathLike
from pathlib import Path

from sonolus.script.engine import Engine
from sonolus.script.level import Level


class Project:
    def __init__(
        self,
        engine: Engine,
        levels: list[Level] | None = None,
        resources: PathLike | None = None,
    ):
        self.engine = engine
        self.levels = levels or []
        self.resources = Path(resources or "resources")
