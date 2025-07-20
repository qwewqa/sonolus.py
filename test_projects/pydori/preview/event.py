from pydori.lib.layer import LAYER_BPM_CHANGE_LINE, LAYER_MEASURE_LINE, LAYER_TIMESCALE_CHANGE_LINE, get_z
from pydori.lib.skin import Skin
from pydori.preview.layout import PREVIEW_BAR_LINE_ALPHA, PreviewData, layout_preview_bar_line, print_at_time
from sonolus.script.archetype import PreviewArchetype, StandardArchetypeName, StandardImport, entity_data, imported
from sonolus.script.printing import PrintColor, PrintFormat
from sonolus.script.timing import beat_to_starting_beat, beat_to_time

# Number of beats between measure lines drawn in the preview.
METER = 4


class PreviewBpmChange(PreviewArchetype):
    name = StandardArchetypeName.BPM_CHANGE

    beat: StandardImport.BEAT = imported()
    bpm: StandardImport.BPM = imported()

    time: float = entity_data()

    def preprocess(self):
        self.time = beat_to_time(self.beat)

    def render(self):
        self.render_bpm_line()
        self.render_succeeding_measure_lines()

    def render_bpm_line(self):
        Skin.bpm_change_line.draw(
            layout_preview_bar_line(self.time, "right"),
            z=get_z(LAYER_BPM_CHANGE_LINE),
            a=PREVIEW_BAR_LINE_ALPHA,
        )
        print_at_time(
            self.bpm,
            self.time,
            fmt=PrintFormat.BPM,
            color=PrintColor.PURPLE,
            side="right",
        )

    def render_succeeding_measure_lines(self):
        beat = self.beat + METER
        while beat_to_starting_beat(beat) == self.beat and beat <= PreviewData.last_beat:
            Skin.measure_line.draw(
                layout_preview_bar_line(beat_to_time(beat), "none"),
                z=LAYER_MEASURE_LINE - beat_to_time(beat) / 100,
                a=PREVIEW_BAR_LINE_ALPHA,
            )
            beat += METER


class PreviewTimescaleChange(PreviewArchetype):
    name = StandardArchetypeName.TIMESCALE_CHANGE

    beat: StandardImport.BEAT = imported()
    timescale: StandardImport.TIMESCALE = imported()

    time: float = entity_data()

    def preprocess(self):
        self.time = beat_to_time(self.beat)

    def render(self):
        Skin.timescale_change_line.draw(
            layout_preview_bar_line(self.time, "left"),
            z=get_z(LAYER_TIMESCALE_CHANGE_LINE),
            a=PREVIEW_BAR_LINE_ALPHA,
        )
        print_at_time(
            self.timescale,
            self.time,
            fmt=PrintFormat.TIMESCALE,
            color=PrintColor.YELLOW,
            side="left",
        )
