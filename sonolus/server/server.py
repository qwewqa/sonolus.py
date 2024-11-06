from pathlib import Path
from typing import Annotated

from litestar import Controller

from sonolus.script.engine import Engine


def run_dev_server(
    engine: Engine,
    skin: str | None = None,
    background: str | None = None,
    effect: str | None = None,
    particle: str | None = None,
    resource_path: Path = Path("resources"),
):
    pass


type ServerItems = Annotated[dict[str, dict]]