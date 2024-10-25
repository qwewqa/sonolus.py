from sonolus.script.comptime import Comptime
from sonolus.script.record import Record


class Skin[Name](Record):
    name: Comptime.of(Name, str)

# TODO
