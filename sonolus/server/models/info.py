from enum import StrEnum
from msgspec import Struct

from sonolus.server.models.item import ServerItemSection
from sonolus.server.models.misc import Srl
from sonolus.server.models.server_option import ServerForm, ServerOption


class ServerInfoButtonType(StrEnum):
    AUTHENTICATION = "authentication"
    MULTIPLAYER = "multiplayer"
    POST = "post"
    PLAYLIST = "playlist"
    LEVEL = "level"
    REPLAY = "replay"
    SKIN = "skin"
    BACKGROUND = "background"
    EFFECT = "effect"
    PARTICLE = "particle"
    ENGINE = "engine"
    CONFIGURATION = "configuration"

class ServerInfoButton(Struct, kw_only=True, omit_defaults=True):
    type: ServerInfoButtonType

class ServerConfiguration(Struct, kw_only=True, omit_defaults=True):
    options: list[ServerOption]

class ServerInfo(Struct, kw_only=True, omit_defaults=True):
    title: str
    description: str | None = None
    buttons: list[ServerInfoButton]
    configuration: ServerConfiguration
    banner: Srl | None = None

class ServerItemInfo(Struct, kw_only=True, omit_defaults=True):
    creates: list[ServerForm] | None = None
    searches: list[ServerForm] | None = None
    sections: list[ServerItemSection]
    banner: Srl | None = None
