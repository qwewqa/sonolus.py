from __future__ import annotations

from pydori.lib.layer import LAYER_ARROW, LAYER_NOTE, get_z
from pydori.lib.note import NoteKind, get_note_arrow_sprite, get_note_body_sprite
from pydori.lib.options import Options
from pydori.preview.layout import (
    PreviewData,
    layout_preview_directional_flick_arrow,
    layout_preview_flick_arrow,
    layout_preview_note,
)
from sonolus.script.archetype import PreviewArchetype, StandardImport, entity_data, imported
from sonolus.script.timing import beat_to_time


class PreviewNote(PreviewArchetype):
    """Common archetype for notes."""

    name = "Note"
    is_scored = True

    kind: NoteKind = imported()
    lane: float = imported()
    beat: StandardImport.BEAT = imported()
    direction: int = imported()

    target_time: float = entity_data()

    def preprocess(self):
        if Options.mirror:
            self.lane = -self.lane
            self.direction = -self.direction

        self.target_time = beat_to_time(self.beat)

        PreviewData.last_time = max(PreviewData.last_time, self.target_time)
        PreviewData.last_beat = max(PreviewData.last_beat, self.beat)

    def render(self):
        self.draw_body()
        self.draw_arrow()

    def draw_body(self):
        if self.kind == NoteKind.HOLD_ANCHOR:
            return
        body_sprite = get_note_body_sprite(self.kind, self.direction)
        layout = layout_preview_note(self.lane, self.target_time)
        body_sprite.draw(layout, z=get_z(LAYER_NOTE, lane=self.lane, y=self.target_time))

    def draw_arrow(self):
        arrow_sprite = get_note_arrow_sprite(self.kind, self.direction)
        match self.kind:
            case NoteKind.FLICK:
                layout = layout_preview_flick_arrow(self.lane, self.target_time)
                arrow_sprite.draw(layout, z=get_z(LAYER_ARROW, lane=self.lane, y=self.target_time))
            case NoteKind.DIRECTIONAL_FLICK:
                for i in range(abs(self.direction)):
                    lane_offset = (i + 1) * (1 if self.direction > 0 else -1)
                    arrow_lane = self.lane + lane_offset
                    layout = layout_preview_directional_flick_arrow(
                        arrow_lane, self.target_time, direction=self.direction
                    )
                    arrow_sprite.draw(layout, z=get_z(LAYER_ARROW, lane=arrow_lane, y=self.target_time))
            case _:
                pass


class PreviewUnscoredNote(PreviewNote):
    """A note that does not contribute to score or judgment.

    Used for hold anchors.
    """

    name = "UnscoredNote"
    is_scored = False
