from msgspec import Struct, field

from sonolus.server.models.leaderboard import ServerItemLeaderboard
from sonolus.server.models.server_option import ServerForm


class ServerItemList[T](Struct, kw_only=True, omit_defaults=True):
    page_count: int = field(name="pageCount")
    items: list[T]
    searches: list[ServerForm] | None = None


class ServerItemSection[T](Struct, kw_only=True, omit_defaults=True):
    title: str
    icon: str | None = None
    description: str | None = None
    help: str | None = None
    items: list[T]
    search: ServerForm | None = None
    search_values: str | None = field(name="searchValues", default=None)


class ServerItemDetails[T](Struct, kw_only=True, omit_defaults=True):
    item: T
    description: str | None = None
    actions: list[ServerForm]
    has_community: bool = field(name="hasCommunity")
    leaderboards: list[ServerItemLeaderboard]
    sections: list[ServerItemSection]
