from typing import ClassVar

from sonolus.script.archetype import Archetype


class EngineMode:
    pass


class PlayMode(EngineMode):
    archetypes: ClassVar[list[type[Archetype]]]
