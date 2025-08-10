from itertools import pairwise

from pydori.play.connector import HoldConnector, SimLine
from pydori.play.event import BpmChange, TimescaleChange
from pydori.play.note import (
    FlickNote,
    HoldAnchorNote,
    HoldEndNote,
    HoldHeadNote,
    HoldTickNote,
    Note,
    TapNote,
)
from pydori.play.stage import Stage
from sonolus.script.archetype import PlayArchetype
from sonolus.script.level import Level, LevelData


def demo_level():
    entities = [
        Stage(),
        BpmChange(
            beat=0,
            bpm=60,
        ),
        TapNote(
            beat=1,
            lane=0,
        ),
        TapNote(
            beat=2,
            lane=-2,
        ),
        TapNote(
            beat=3,
            lane=2,
        ),
        TapNote(
            beat=3,
            lane=0,
        ),
        TimescaleChange(
            beat=4.5,
            timescale=0.5,
        ),
        TapNote(
            beat=5,
            lane=-2,
        ),
        TapNote(
            beat=5,
            lane=0,
        ),
        TapNote(
            beat=5,
            lane=2,
        ),
        TimescaleChange(
            beat=6.5,
            timescale=0.2,
        ),
        BpmChange(
            beat=6,
            bpm=120,
        ),
        hold(
            HoldHeadNote(
                beat=7,
                lane=-3,
            ),
            HoldTickNote(
                beat=7.25,
                lane=-2,
            ),
            HoldTickNote(
                beat=7.5,
                lane=-1,
            ),
            HoldTickNote(
                beat=7.75,
                lane=0,
            ),
            HoldTickNote(
                beat=8,
                lane=1,
            ),
            HoldTickNote(
                beat=8.25,
                lane=2,
            ),
            HoldEndNote(
                beat=8.5,
                lane=3,
            ),
        ),
        TimescaleChange(
            beat=8.5,
            timescale=1.2,
        ),
        hold(
            HoldHeadNote(
                beat=9,
                lane=-3,
            ),
            HoldAnchorNote(
                beat=9.5,
                lane=3,
            ),
            FlickNote(
                beat=10,
                lane=-2,
            ),
        ),
    ]

    return Level(
        name="pydori_level",
        title="pydori Level",
        bgm=None,
        data=LevelData(
            bgm_offset=0,
            entities=[
                *entities,
                *create_sim_lines(entities),
            ],
        ),
    )


def hold(
    *notes: Note,
) -> list[PlayArchetype]:
    """Update the notes to reference each other and create connectors, then return notes and connectors in a list."""
    connectors = []
    for a, b in pairwise(sorted(notes, key=lambda n: n.beat)):
        connectors.append(HoldConnector(first_ref=a.ref(), second_ref=b.ref()))
        b.prev_ref = a.ref()
        a.next_ref = b.ref()
    return [
        *notes,
        *connectors,
    ]


def create_sim_lines(entities: list[PlayArchetype]) -> list[PlayArchetype]:
    """Create sim lines for the given entities."""
    if not entities:
        return []

    notes = [n for n in entities if isinstance(n, Note) and not isinstance(n, HoldTickNote | HoldAnchorNote)]

    notes.sort(key=lambda n: (n.beat, n.lane))
    sim_lines = []
    for a, b in pairwise(notes):
        if a.beat == b.beat:
            sim_lines.append(SimLine(first_ref=a.ref(), second_ref=b.ref()))

    return sim_lines
