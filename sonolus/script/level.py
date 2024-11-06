from __future__ import annotations

from sonolus.build.collection import Asset
from sonolus.script.archetype import PlayArchetype, StandardImport


class Level:
    version = 1

    def __init__(
        self,
        *,
        name: str,
        title: str | None = None,
        rating: int = 0,
        artists: str = "Unknown",
        author: str = "Unknown",
        cover: Asset | None = None,
        bgm: Asset | None = None,
        data: LevelData,
    ) -> None:
        self.name = name
        self.title = title or name
        self.rating = rating
        self.artists = artists
        self.author = author
        self.cover = cover
        self.bgm = bgm
        self.data = data


class LevelData:
    bgm_offset: float
    entities: list[PlayArchetype]

    def __init__(self, bgm_offset: float, entities: list[PlayArchetype]) -> None:
        self.bgm_offset = bgm_offset
        self.entities = entities


class BpmChange(PlayArchetype):
    name = "#BPM_CHANGE"

    beat: StandardImport.Beat
    bpm: StandardImport.Bpm


class TimescaleChange(PlayArchetype):
    name = "#TIMESCALE_CHANGE"

    beat: StandardImport.Beat
    timescale: StandardImport.Timescale
