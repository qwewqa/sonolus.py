"""Tests for what a mode definition accepts and for what it keeps.

A mode is built once, at import time, and read many times afterwards: by the checks in its own constructor, and
then by each stage of the build. Whatever the constructor stores has to survive all of those reads, so an
argument that can only be walked once has to be materialized before it is stored.
"""

import pytest

from sonolus.script.archetype import PlayArchetype, PreviewArchetype, WatchArchetype
from sonolus.script.effect import effects
from sonolus.script.engine import PlayMode, PreviewMode, TutorialMode, WatchMode
from sonolus.script.instruction import instruction_icons, instructions
from sonolus.script.sprite import skin


class ModeNote(PlayArchetype):
    name = "ModeNote"


class ModeWatchNote(WatchArchetype):
    name = "ModeWatchNote"


class ModePreviewNote(PreviewArchetype):
    name = "ModePreviewNote"


def test_play_mode_keeps_the_archetypes_of_a_one_shot_iterable():
    mode = PlayMode(archetypes=(archetype for archetype in [ModeNote]))

    assert mode.archetypes == [ModeNote]


def test_watch_mode_keeps_the_archetypes_of_a_one_shot_iterable():
    mode = WatchMode(archetypes=(archetype for archetype in [ModeWatchNote]), update_spawn=lambda: 0.0)

    assert mode.archetypes == [ModeWatchNote]


def test_preview_mode_keeps_the_archetypes_of_a_one_shot_iterable():
    mode = PreviewMode(archetypes=(archetype for archetype in [ModePreviewNote]))

    assert mode.archetypes == [ModePreviewNote]


def test_a_mode_can_be_read_more_than_once():
    mode = PlayMode(archetypes=(archetype for archetype in [ModeNote]))

    assert [list(mode.archetypes), list(mode.archetypes)] == [[ModeNote], [ModeNote]]


def test_omitted_archetypes_are_an_empty_list():
    assert [PlayMode().archetypes, WatchMode(update_spawn=lambda: 0.0).archetypes, PreviewMode().archetypes] == [
        [],
        [],
        [],
    ]


@skin
class ModeSkin:
    pass


@effects
class ModeEffects:
    pass


@instructions
class ModeInstructions:
    pass


@instruction_icons
class ModeInstructionIcons:
    pass


def _noop() -> None:
    pass


def _tutorial_mode(**resources) -> TutorialMode:
    return TutorialMode(preprocess=_noop, navigate=_noop, update=_noop, **resources)


@pytest.mark.parametrize(
    ("construct", "message", "named"),
    [
        (lambda: PlayMode(skin=ModeEffects), "Invalid skin", "ModeEffects"),
        (lambda: PlayMode(effects=ModeSkin), "Invalid effects", "ModeSkin"),
        (lambda: PlayMode(particles=ModeSkin), "Invalid particles", "ModeSkin"),
        (lambda: PlayMode(buckets=ModeSkin), "Invalid buckets", "ModeSkin"),
        (lambda: _tutorial_mode(instructions=ModeInstructionIcons), "Invalid instructions", "ModeInstructionIcons"),
        (
            lambda: _tutorial_mode(instruction_icons=ModeInstructions),
            "Invalid instruction icons",
            "ModeInstructions",
        ),
    ],
)
def test_a_mode_given_a_resource_of_the_wrong_kind_names_its_type(construct, message, named):
    # Every resource decorator returns an instance, so the value one of these guards rejects is one whose repr
    # is `<Cls object at 0xADDRESS>` unless the guard names its type instead.
    with pytest.raises(ValueError, match=message) as exc_info:
        construct()

    text = str(exc_info.value)
    assert named in text
    assert "0x" not in text


def test_a_mode_given_an_archetype_of_another_mode_names_the_archetype():
    with pytest.raises(ValueError, match="is not a PlayArchetype") as exc_info:
        PlayMode(archetypes=[ModeWatchNote])

    text = str(exc_info.value)
    assert "ModeWatchNote" in text
    assert "<class" not in text


def test_a_mode_given_an_undecorated_class_names_the_class():
    class NotASkin:
        pass

    with pytest.raises(ValueError, match="Invalid skin") as exc_info:
        PlayMode(skin=NotASkin)

    assert "NotASkin" in str(exc_info.value)
