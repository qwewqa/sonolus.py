from sonolus.script.particle import StandardParticle, particles


@particles
class Particles:
    lane: StandardParticle.LANE_LINEAR

    tap_linear: StandardParticle.NOTE_LINEAR_TAP_CYAN
    tap_circular: StandardParticle.NOTE_CIRCULAR_TAP_CYAN

    hold_linear: StandardParticle.NOTE_LINEAR_TAP_GREEN
    hold_circular: StandardParticle.NOTE_CIRCULAR_TAP_GREEN
    hold_active_circular: StandardParticle.NOTE_CIRCULAR_HOLD_GREEN

    flick_linear: StandardParticle.NOTE_LINEAR_ALTERNATIVE_RED
    flick_circular: StandardParticle.NOTE_CIRCULAR_ALTERNATIVE_RED

    right_flick_linear: StandardParticle.NOTE_LINEAR_ALTERNATIVE_YELLOW
    right_flick_circular: StandardParticle.NOTE_CIRCULAR_ALTERNATIVE_YELLOW

    left_flick_linear: StandardParticle.NOTE_LINEAR_ALTERNATIVE_PURPLE
    left_flick_circular: StandardParticle.NOTE_CIRCULAR_ALTERNATIVE_PURPLE
