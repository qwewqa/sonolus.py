from __future__ import annotations

from os import PathLike
from pathlib import Path
from typing import Self, TypedDict

from sonolus.script.archetype import ArchetypeSchema
from sonolus.script.engine import Engine
from sonolus.script.level import Level


class Project:
    """A Sonolus.py project.

    Args:
        engine: The engine of the project.
        levels: The levels of the project.
        resources: The path to the resources of the project.
    """

    def __init__(
        self,
        engine: Engine,
        levels: list[Level] | None = None,
        resources: PathLike | None = None,
    ):
        self.engine = engine
        self.levels = levels or []
        self.resources = Path(resources or "resources")

    def with_levels(self, levels: list[Level]) -> Self:
        """Create a new project with the specified levels.

        Args:
            levels: The levels of the project.

        Returns:
            The new project.
        """
        return Project(self.engine, levels, self.resources)

    def dev(self, build_dir: PathLike, port: int = 8080):
        """Start a development server for the project.

        Args:
            build_dir: The path to the build directory.
            port: The port of the development server.
        """
        from sonolus.build.cli import build_collection, run_server

        build_collection(self, Path(build_dir))
        run_server(Path(build_dir) / "site", port=port)

    def build(self, build_dir: PathLike):
        """Build the project.

        Args:
            build_dir: The path to the build directory.
        """
        from sonolus.build.cli import build_project

        build_project(self, Path(build_dir))

    def schema(self) -> ProjectSchema:
        """Generate the schema of the project.

        Returns:
            The schema of the project.
        """
        from sonolus.build.project import get_project_schema

        return get_project_schema(self)


class ProjectSchema(TypedDict):
    archetypes: list[ArchetypeSchema]
