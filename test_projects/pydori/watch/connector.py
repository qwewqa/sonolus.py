from pydori.lib.connector import draw_hold_connector, draw_sim_line
from pydori.watch.note import WatchNote
from sonolus.script.archetype import EntityRef, WatchArchetype, imported
from sonolus.script.interval import remap
from sonolus.script.runtime import time


class WatchHoldConnector(WatchArchetype):
    """A connector for hold notes."""

    name = "HoldConnector"

    first_ref: EntityRef[WatchNote] = imported()
    second_ref: EntityRef[WatchNote] = imported()

    end_time: float = imported()

    def spawn_time(self) -> float:
        return self.first.spawn_time()

    def despawn_time(self) -> float:
        return self.second.target_scaled_time

    def update_sequential(self):
        if self.first.target_time <= time() < self.second.target_time and self.head.has_active_touch:
            self.head.hold_lane = remap(self.first.y, self.second.y, self.first.lane, self.second.lane, 0)

    def update_parallel(self):
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


class WatchSimLine(WatchArchetype):
    """A line connecting two simultaneous notes."""

    name = "SimLine"

    first_ref: EntityRef[WatchNote] = imported()
    second_ref: EntityRef[WatchNote] = imported()

    def spawn_time(self) -> float:
        return self.first.spawn_time()

    def despawn_time(self) -> float:
        return min(self.first.despawn_time(), self.second.despawn_time())

    def update_parallel(self):
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
