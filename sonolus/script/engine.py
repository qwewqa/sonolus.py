from typing import ClassVar

from sonolus.script.archetype import BaseArchetype


class EngineMode:
    pass


class PlayMode(EngineMode):
    archetypes: ClassVar[list[type[BaseArchetype]]]
