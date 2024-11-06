from sonolus.backend.ops import Op
from sonolus.backend.place import BlockPlace, Place, SSAPlace

type IRExpr = IRConst | IRPureInstr | IRGet
type IRStmt = IRExpr | IRInstr | IRSet


class IRConst:
    value: float

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


class IRPureInstr:
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


class IRInstr:
    op: Op
    args: list[IRExpr]

    def __init__(self, op: Op, args: list[IRExpr]):
        self.op = op
        self.args = args

    def __repr__(self):
        return f"IRInstr({self.op!r}, {self.args!r})"

    def __str__(self):
        return f"{self.op.name}({', '.join(map(str, self.args))})"


class IRGet:
    place: Place

    def __init__(self, place: Place):
        self.place = place

    def __repr__(self):
        return f"IRGet({self.place!r})"

    def __str__(self):
        return f"{self.place}"


class IRSet:
    place: Place
    value: IRStmt

    def __init__(self, place: Place, value: IRStmt):
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
