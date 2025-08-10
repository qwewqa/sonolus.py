from pydori.lib.skin import Skin
from pydori.preview.connector import PreviewHoldConnector, PreviewSimLine
from pydori.preview.event import PreviewBpmChange, PreviewTimescaleChange
from pydori.preview.note import ALL_PREVIEW_NOTE_TYPES
from pydori.preview.stage import PreviewStage
from sonolus.script.engine import PreviewMode

preview_mode = PreviewMode(
    archetypes=[
        PreviewStage,
        *ALL_PREVIEW_NOTE_TYPES,
        PreviewHoldConnector,
        PreviewSimLine,
        PreviewBpmChange,
        PreviewTimescaleChange,
    ],
    skin=Skin,
)
