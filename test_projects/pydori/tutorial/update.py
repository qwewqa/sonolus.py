from pydori.lib.stage import draw_stage
from pydori.tutorial.framework import current_phase_time, reset_phase, update_end, update_start
from pydori.tutorial.phases import PHASES
from sonolus.script.globals import level_memory


@level_memory
class TutorialState:
    current_phase: int


def inc_phase():
    TutorialState.current_phase += 1
    TutorialState.current_phase %= len(PHASES)
    reset_phase()


def dec_phase():
    TutorialState.current_phase -= 1
    TutorialState.current_phase %= len(PHASES)
    reset_phase()


def run_current_phase():
    for i, phase in enumerate(PHASES):
        if i == TutorialState.current_phase:
            is_done = phase(current_phase_time())
            if is_done:
                inc_phase()
            return


def update():
    update_start()
    draw_stage()
    run_current_phase()
    update_end()
