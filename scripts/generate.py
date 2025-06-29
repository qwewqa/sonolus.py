import json
from pathlib import Path
from typing import TextIO

functions_file = "Functions.json"
block_files = {
    "Tutorial": "Engine/Tutorial/Blocks.json",
    "Play": "Level/Play/Blocks.json",
    "Preview": "Level/Preview/Blocks.json",
    "Watch": "Level/Watch/Blocks.json",
}
base_dir = Path(__file__).parent
out_dir = base_dir / "out"
runtimes_dir = base_dir / "runtimes"


def functions():
    with (
        (runtimes_dir / functions_file).open("r", encoding="utf-8") as f,
        (out_dir / "ops.py").open("w", encoding="utf-8") as out,
    ):
        data = json.load(f)
        out.write(
            "from enum import Enum\n"
            "\n"
            "\n"
            "class Op(str, Enum):\n"
            "    def __new__(cls, name: str, side_effects: bool, pure: bool, control_flow: bool):\n"
            "        obj = str.__new__(cls, name)\n"
            "        obj._value_ = name\n"
            "        obj.side_effects = side_effects\n"
            "        obj.pure = pure\n"
            "        obj.control_flow = control_flow\n"
            "        return obj\n"
            "\n"
        )
        for function in data:
            name = function["name"]
            side_effects = function["sideEffects"]
            pure = function["pure"]
            control_flow = function["controlFlow"]
            out.write(f'    {name} = ("{name}", {side_effects}, {pure}, {control_flow})\n')


def block(name: str, f: TextIO, out: TextIO):
    data = json.load(f)
    out.write(f"class {name}Block(_Block, Enum):\n")
    for block in data:
        name = block["name"]
        id_ = block["id"]
        readable = block["readable"]
        writable = block["writable"]
        out.write(
            f"    {name} = ({id_}, {{{', '.join(f'"{e}"' for e in readable)}}}, {{{
                ', '.join(f'"{e}"' for e in writable)
            }}})\n"
        )


def blocks():
    with (out_dir / "blocks.py").open("w", encoding="utf-8") as out:
        out.write(
            "from enum import Enum\n"
            "\n"
            "\n"
            "class _Block(int):\n"
            "    def __new__(cls, id_: int, readable: set[str], writable: set[str]):\n"
            "        obj = int.__new__(cls, id_)\n"
            "        obj.readable = readable\n"
            "        obj.writable = writable\n"
            "        return obj\n"
        )
        for name, file in block_files.items():
            out.write("\n\n")
            with (
                (runtimes_dir / file).open("r", encoding="utf-8") as f,
            ):
                block(name, f, out)
        out.write(f"\n\ntype Block = {' | '.join(f'{name}Block' for name in block_files)}\n")


def main():
    out_dir.mkdir(exist_ok=True)
    functions()
    blocks()


if __name__ == "__main__":
    main()
