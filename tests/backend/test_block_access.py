from sonolus.backend.mode import Mode
from sonolus.build.compile import callback_to_cfg
from sonolus.script.archetype import WatchArchetype
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks


def test_watch_entity_input_writable_outside_preprocess():
    # WatchBlock.EntityInput is writable in every archetype callback (not just preprocess)
    # per the Sonolus watch-mode spec. Writing self.result outside preprocess must compile.
    class W(WatchArchetype):
        def update_sequential(self):
            self.result.bucket_value = 1.0

    W._init_fields()
    project_state = ProjectContextState(runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE)
    mode_state = ModeContextState(Mode.WATCH, [W])
    # Before the fix this raised RuntimeError("Block ... is not writable in updateSequential").
    cfg = callback_to_cfg(project_state, mode_state, W.update_sequential, "updateSequential", W)
    assert cfg is not None
