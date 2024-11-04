from dataclasses import dataclass

from sonolus.script.archetype import PlayArchetype, StandardImport


@dataclass
class LevelData:
    bgm_offset: float
    entities: list[PlayArchetype]


class BpmChange(PlayArchetype):
    name = "#BPM_CHANGE"

    beat: StandardImport.Beat
    bpm: StandardImport.Bpm


class TimescaleChange(PlayArchetype):
    name = "#TIMESCALE_CHANGE"

    beat: StandardImport.Beat
    timescale: StandardImport.Timescale
