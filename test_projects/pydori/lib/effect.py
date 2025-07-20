from sonolus.script.effect import StandardEffect, effects

# The same sfx played within this many seconds of the previous one will not be played,
# to prevent them from clashing and sounding unpleasant.
SFX_DISTANCE = 0.02


@effects
class Effects:
    # Empty tap.
    stage: StandardEffect.STAGE

    # Non-flick notes.
    perfect: StandardEffect.PERFECT
    great: StandardEffect.GREAT
    good: StandardEffect.GOOD

    # Flick notes.
    perfect_alt: StandardEffect.PERFECT_ALTERNATIVE
    great_alt: StandardEffect.GREAT_ALTERNATIVE
    good_alt: StandardEffect.GOOD_ALTERNATIVE

    # Active hold.
    hold: StandardEffect.HOLD
