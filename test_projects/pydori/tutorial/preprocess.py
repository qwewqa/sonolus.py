from pydori.lib.layout import init_layout
from pydori.lib.stage import init_stage_data
from pydori.lib.ui import init_ui


def preprocess():
    init_layout()
    init_ui()
    init_stage_data()
