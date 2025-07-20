from pydori.lib.connector import draw_hold_connector, draw_sim_line
from pydori.play.note import Note
from sonolus.script.archetype import EntityRef, PlayArchetype, imported
from sonolus.script.interval import remap
from sonolus.script.runtime import time


class HoldConnector(PlayArchetype):
    """A connector for hold notes."""

    name = "HoldConnector"

    first_ref: EntityRef[Note] = imported()
    second_ref: EntityRef[Note] = imported()

    def should_spawn(self) -> bool:
        return self.first.should_spawn()

    def spawn_order(self) -> float:
        return self.first.spawn_order()

    def update_sequential(self):
        if time() >= self.second.target_time:
            # The target time of the second note has passed, so this connector is no longer needed.
            self.despawn = True
            return
        if self.first.target_time <= time() < self.second.target_time:
            # This connector is the one currently crossing the judgment line,
            # so it has the information to calculate which lane the hold is currently crossing the judgment line at.
            # The hold lane is stored in the note head so the hold manager can use it draw the hold particle and
            # note head at the correct lane.
            self.head.hold_lane = remap(self.first.y, self.second.y, self.first.lane, self.second.lane, 0)

    def update_parallel(self):
        if self.despawn:
            return
        draw_hold_connector(
            self.first.lane,
            self.second.lane,
            self.first.y,
            self.second.y,
        )

    @property
    def first(self):
        return self.first_ref.get()

    @property
    def second(self):
        return self.second_ref.get()

    @property
    def head(self):
        return self.first.head


class SimLine(PlayArchetype):
    """A line connecting two simultaneous notes."""

    name = "SimLine"

    first_ref: EntityRef[Note] = imported()
    second_ref: EntityRef[Note] = imported()

    def should_spawn(self) -> bool:
        return self.first.should_spawn()

    def spawn_order(self) -> float:
        return self.first.spawn_order()

    def update_parallel(self):
        if self.first.is_despawned or self.second.is_despawned:
            self.despawn = True
            return
        draw_sim_line(
            self.first.lane,
            self.second.lane,
            self.first.y,
        )

    @property
    def first(self):
        return self.first_ref.get()

    @property
    def second(self):
        return self.second_ref.get()
