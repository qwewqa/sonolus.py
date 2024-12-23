from collections.abc import Iterator
from typing import Self

from sonolus.backend.blocks import Block

type Place = BlockPlace | SSAPlace
type BlockValue = Block | int | TempBlock | Place
type IndexValue = int | Place


class TempBlock:
    __slots__ = ("__hash", "name", "size")

    def __init__(self, name: str, size: int = 1):
        self.name = name
        self.size = size
        self.__hash = hash(name)  # Precompute hash based on name alone

    def __repr__(self):
        return f"TempBlock(name={self.name!r}, size={self.size!r})"

    def __str__(self):
        return f"{self.name}"

    def __getitem__(self, item) -> "BlockPlace":
        return BlockPlace(self, item)

    def __iter__(self) -> "Iterator[BlockPlace]":
        for i in range(self.size):
            yield self[i]

    def __eq__(self, other):
        return isinstance(other, TempBlock) and self.name == other.name and self.size == other.size

    def __lt__(self, other):
        if not isinstance(other, TempBlock):
            return NotImplemented
        return str(self) < str(other)

    def __le__(self, other):
        if not isinstance(other, TempBlock):
            return NotImplemented
        return str(self) <= str(other)

    def __gt__(self, other):
        if not isinstance(other, TempBlock):
            return NotImplemented
        return str(self) > str(other)

    def __ge__(self, other):
        if not isinstance(other, TempBlock):
            return NotImplemented
        return str(self) >= str(other)

    def __hash__(self):
        return self.__hash


class BlockPlace:
    __slots__ = ("__hash", "block", "index", "offset")

    def __init__(self, block: BlockValue, index: IndexValue = 0, offset: int = 0):
        self.block = block
        self.index = index
        self.offset = offset
        self.__hash = hash((block, index, offset))

    def __repr__(self):
        return f"BlockPlace(block={self.block!r}, index={self.index!r}, offset={self.offset!r})"

    def __str__(self):
        if isinstance(self.block, TempBlock) and self.block.size == 1 and self.index == 0 and self.offset == 0:
            return f"{self.block}"
        elif isinstance(self.index, int):
            return f"{self.block}[{self.index + self.offset}]"
        elif self.offset == 0:
            return f"{self.block}[{self.index}]"
        else:
            return f"{self.block}[{self.index} + {self.offset}]"

    def add_offset(self, offset: int) -> Self:
        return BlockPlace(self.block, self.index, self.offset + offset)

    def __eq__(self, other):
        return (
            isinstance(other, BlockPlace)
            and self.block == other.block
            and self.index == other.index
            and self.offset == other.offset
        )

    def __lt__(self, other):
        if not isinstance(other, BlockPlace):
            return NotImplemented
        return str(self) < str(other)

    def __le__(self, other):
        if not isinstance(other, BlockPlace):
            return NotImplemented
        return str(self) <= str(other)

    def __gt__(self, other):
        if not isinstance(other, BlockPlace):
            return NotImplemented
        return str(self) > str(other)

    def __ge__(self, other):
        if not isinstance(other, BlockPlace):
            return NotImplemented
        return str(self) >= str(other)

    def __hash__(self):
        return self.__hash

    def __iter__(self):
        yield self.block
        yield self.index
        yield self.offset


class SSAPlace:
    __slots__ = ("__hash", "name", "num")

    def __init__(self, name: str, num: int):
        self.name = name
        self.num = num
        self.__hash = hash((name, num))

    def __repr__(self):
        return f"SSAPlace(name={self.name!r}, num={self.num!r})"

    def __str__(self):
        return f"{self.name}.{self.num}"

    def __eq__(self, other):
        return isinstance(other, SSAPlace) and self.name == other.name and self.num == other.num

    def __lt__(self, other):
        if not isinstance(other, SSAPlace):
            return NotImplemented
        return str(self) < str(other)

    def __le__(self, other):
        if not isinstance(other, SSAPlace):
            return NotImplemented
        return str(self) <= str(other)

    def __gt__(self, other):
        if not isinstance(other, SSAPlace):
            return NotImplemented
        return str(self) > str(other)

    def __ge__(self, other):
        if not isinstance(other, SSAPlace):
            return NotImplemented
        return str(self) >= str(other)

    def __hash__(self):
        return self.__hash

    def __iter__(self):
        yield self.name
        yield self.num
