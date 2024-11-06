from msgspec import Struct, field

class ServerItemLeaderboardRecord(Struct, kw_only=True, omit_defaults=True):
    name: str
    rank: str
    player: str
    value: str

class ServerItemLeaderboardDetails(Struct, kw_only=True, omit_defaults=True):
    top_records: list[ServerItemLeaderboardRecord] = field(name="topRecords")

class ServerItemLeaderboard(Struct, kw_only=True, omit_defaults=True):
    name: str
    title: str
    description: str | None = None

class ServerItemLeaderboardRecordDetails(Struct, kw_only=True, omit_defaults=True):
    replays: list[ReplayItem]

class ServerItemLeaderboardRecordList(Struct, kw_only=True, omit_defaults=True):
    page_count: int = field(name="pageCount")
    records: list[ServerItemLeaderboardRecord]
