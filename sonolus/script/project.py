from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import ClassVar, TypedDict

from sonolus.backend.optimize import optimize
from sonolus.backend.optimize.passes import CompilerPass
from sonolus.script.archetype import ArchetypeSchema
from sonolus.script.engine import Engine
from sonolus.script.level import ExternalLevelData, Level, LevelData


class Project:
    """A Sonolus.py project.

    Args:
        engine: The engine of the project.
        levels: The levels of the project.
        resources: The path to the resources of the project.
        converters: A dictionary mapping engine names to converter functions, for converting loaded levels.
    """

    def __init__(
        self,
        engine: Engine,
        levels: Iterable[Level] | Callable[[], Iterable[Level]] | None = None,
        resources: PathLike | None = None,
        converters: dict[str | None, Callable[[ExternalLevelData], LevelData | None]] | None = None,
    ):
        self.engine = engine
        match levels:
            case Callable():
                self._level_source = lazy_loader(levels)
            case Iterable():
                self._level_source = levels
            case None:
                self._level_source = []
            case _:
                raise TypeError(f"Invalid type for levels: {type(levels)}. Expected Iterable or Callable.")
        self._levels = None
        self.resources = Path(resources or "resources")
        self.converters = converters or {}

    def with_levels(self, levels: Iterable[Level] | Callable[[], Iterable[Level]] | None) -> Project:
        """Create a new project with the specified levels.

        Args:
            levels: The levels of the project.

        Returns:
            The new project.
        """
        return Project(self.engine, levels, self.resources)

    def dev(self, build_dir: PathLike, port: int = 8080, config: BuildConfig | None = None):
        """Start a development server for the project.

        Args:
            build_dir: The path to the build directory.
            port: The port of the development server.
            config: The build configuration.
        """
        from sonolus.build.cli import run_server

        if config is None:
            config = BuildConfig()

        run_server(
            Path(build_dir) / "site",
            port=port,
            project_module_name=None,
            core_module_names=None,
            build_dir=Path(build_dir),
            config=config,
            project=self,
        )

    def build(self, build_dir: PathLike, config: BuildConfig | None = None):
        """Build the project.

        Args:
            build_dir: The path to the build directory.
            config: The build configuration.
        """
        from sonolus.build.cli import build_project

        config = config or BuildConfig()
        build_project(self, Path(build_dir), config)

    def schema(self) -> ProjectSchema:
        """Generate the schema of the project.

        Returns:
            The schema of the project.
        """
        from sonolus.build.project import get_project_schema

        return get_project_schema(self)

    @property
    def levels(self) -> list[Level]:
        if self._levels is None:
            self._levels = list(self._level_source)
        return self._levels


def lazy_loader(fn):
    yield from fn()


class ProjectSchema(TypedDict):
    archetypes: list[ArchetypeSchema]


@dataclass
class BuildConfig:
    """A configuration for building an engine package."""

    MINIMAL_PASSES: ClassVar[Sequence[CompilerPass]] = optimize.MINIMAL_PASSES
    """The minimal list of compiler passes."""

    FAST_PASSES: ClassVar[Sequence[CompilerPass]] = optimize.FAST_PASSES
    """The list of compiler passes for faster builds."""

    STANDARD_PASSES: ClassVar[Sequence[CompilerPass]] = optimize.STANDARD_PASSES
    """The standard list of compiler passes."""

    passes: Sequence[CompilerPass] = optimize.STANDARD_PASSES
    """The list of compiler passes to use."""

    build_play: bool = True
    """Whether to build the play package."""

    build_watch: bool = True
    """Whether to build the watch package."""

    build_preview: bool = True
    """Whether to build the preview package."""

    build_tutorial: bool = True
    """Whether to build the tutorial package."""

    override_resource_level_engines: bool = True
    """Whether to override any levels included in resources to use the engine of this project."""
