from typing import Any

from sonolus.script.internal.context import ReadOnlyMemory


class Compiler:
    rom: ReadOnlyMemory
    const_mappings: dict[Any, int]
