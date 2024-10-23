from sonolus.script.record import Record
from sonolus.script.vec import Vec2


class Rect(Record):
    t: float
    r: float
    b: float
    l: float  # noqa: E741

    @property
    def w(self) -> float:
        return self.r - self.l

    @property
    def h(self) -> float:
        return self.b - self.t

    @property
    def tl(self) -> Vec2:
        return Vec2(self.l, self.t)

    @property
    def tr(self) -> Vec2:
        return Vec2(self.r, self.t)

    @property
    def br(self) -> Vec2:
        return Vec2(self.r, self.b)

    @property
    def bl(self) -> Vec2:
        return Vec2(self.l, self.b)
