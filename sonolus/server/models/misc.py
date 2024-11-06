from enum import StrEnum
from typing import Never

from msgspec import Struct

type Unsupported = Never

class ItemType(StrEnum):
    POST = "post"
    PLAYLIST = "playlist"
    LEVEL = "level"
    SKIN = "skin"
    BACKGROUND = "background"
    EFFECT = "effect"
    PARTICLE = "particle"
    ENGINE = "engine"
    REPLAY = "replay"
    ROOM = "room"

class Sil(Struct, kw_only=True, omit_defaults=True):
    address: str
    name: str

class Srl(Struct, kw_only=True, omit_defaults=True):
    hash: str | None = None
    url: str | None = None

class Tag(Struct, kw_only=True, omit_defaults=True):
    title: str
    icon: str | None = None