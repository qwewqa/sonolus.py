from sonolus.backend.ops import Op
from sonolus.backend.place import BlockPlace, Place, SSAPlace

type IRExpr = IRConst | IRPureInstr | IRGet
type IRStmt = IRExpr | IRInstr | IRSet


_IR_CONST_CACHE_START = -5
_IR_CONST_CACHE_STOP = 257


class IRConst:
    __slots__ = ("value",)

    value: float | int

    def __new__(cls, value):
        if float(value).is_integer():
            int_value = int(value)
            if _IR_CONST_CACHE_START <= int_value < _IR_CONST_CACHE_STOP:
                return _IR_CONST_CACHE[int_value - _IR_CONST_CACHE_START]
            else:
                return _create_raw_const(int_value)
        return super().__new__(cls)

    def __init__(self, value: float):
        self.value = value

    def __repr__(self):
        return f"IRConst({self.value!r})"

    def __str__(self):
        return f"{self.value}"

    def __eq__(self, other):
        return isinstance(other, IRConst) and self.value == other.value

    def __hash__(self):
        return hash(self.value)


def _create_raw_const(value: float | int) -> IRConst:
    result = object.__new__(IRConst)
    result.value = value
    return result


_IR_CONST_CACHE = tuple(_create_raw_const(i) for i in range(_IR_CONST_CACHE_START, _IR_CONST_CACHE_STOP))


class IRPureInstr:
    __slots__ = ("args", "array_defs", "defs", "is_array_init", "live", "op", "uses", "visited")

    op: Op
    args: list[IRExpr]

    def __init__(self, op: Op, args: list[IRExpr]):
        assert op.pure
        self.op = op
        self.args = args

    def __repr__(self):
        return f"IRPureInstr({self.op!r}, {self.args!r})"

    def __str__(self):
        return f"{self.op.name}({', '.join(map(str, self.args))})"

    def __eq__(self, other):
        return isinstance(other, IRPureInstr) and self.op == other.op and self.args == other.args

    def __hash__(self):
        return hash((self.op, tuple(self.args)))


class IRInstr:
    __slots__ = ("args", "array_defs", "defs", "is_array_init", "live", "op", "uses", "visited")

    op: Op
    args: list[IRExpr]

    def __init__(self, op: Op, args: list[IRExpr]):
        self.op = op
        self.args = args

    def __repr__(self):
        return f"IRInstr({self.op!r}, {self.args!r})"

    def __str__(self):
        return f"{self.op.name}({', '.join(map(str, self.args))})"

    def __eq__(self, other):
        return isinstance(other, IRInstr) and self.op == other.op and self.args == other.args

    def __hash__(self):
        return hash((self.op, tuple(self.args)))


class IRGet:
    __slots__ = ("array_defs", "defs", "is_array_init", "live", "place", "uses", "visited")

    place: Place

    def __init__(self, place: Place):
        self.place = place

    def __repr__(self):
        return f"IRGet({self.place!r})"

    def __str__(self):
        return f"{self.place}"

    def __eq__(self, other):
        return isinstance(other, IRGet) and self.place == other.place

    def __hash__(self):
        return hash(self.place)


class IRSet:
    __slots__ = ("array_defs", "defs", "is_array_init", "live", "place", "uses", "value", "visited")

    place: Place
    value: IRExpr | IRInstr

    def __init__(self, place: Place, value: IRExpr | IRInstr):
        self.place = place
        self.value = value

    def __repr__(self):
        return f"IRSet({self.place!r}, {self.value!r})"

    def __str__(self):
        match self.place:
            case BlockPlace():
                return f"{self.place} <- {self.value}"
            case SSAPlace():
                return f"{self.place} := {self.value}"
            case _:
                raise TypeError(f"Invalid place: {self.place}")

    def __eq__(self, other):
        return isinstance(other, IRSet) and self.place == other.place and self.value == other.value

    def __hash__(self):
        return hash((self.place, self.value))
