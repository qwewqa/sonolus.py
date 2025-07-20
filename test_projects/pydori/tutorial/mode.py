from pydori.lib.effect import Effects
from pydori.lib.particle import Particles
from pydori.lib.skin import Skin
from pydori.tutorial.instructions import InstructionIcons, Instructions
from pydori.tutorial.navigate import navigate
from pydori.tutorial.preprocess import preprocess
from pydori.tutorial.update import update
from sonolus.script.engine import TutorialMode

tutorial_mode = TutorialMode(
    skin=Skin,
    effects=Effects,
    particles=Particles,
    instructions=Instructions,
    instruction_icons=InstructionIcons,
    preprocess=preprocess,
    navigate=navigate,
    update=update,
)
