from typing import ClassVar

from sonolus.script.archetype import BaseArchetype
from sonolus.script.sprite import Skin


class EngineMode:
    pass


class PlayMode(EngineMode):
    archetypes: ClassVar[list[type[BaseArchetype]]]
    skin: ClassVar[Skin]
