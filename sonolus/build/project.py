from pathlib import Path

from sonolus.build.collection import Asset, Collection, Srl
from sonolus.build.engine import package_engine
from sonolus.build.level import package_level_data
from sonolus.script.engine import Engine
from sonolus.script.level import Level
from sonolus.script.project import Project

BLANK_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x01\x00\x00\x00\x007n\xf9$"
    b"\x00\x00\x00\nIDATx\x01c`\x00\x00\x00\x02\x00\x01su\x01\x18\x00\x00\x00\x00IEND\xaeB`\x82"
)
BLANK_AUDIO = (
    b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02"
    b"\x00\x10\x00data\x00\x00\x00\x00"
)


def build_project_to_collection(project: Project):
    collection = load_resources_files_to_collection(project.resources)
    add_engine_to_collection(collection, project, project.engine)
    for level in project.levels:
        add_level_to_collection(collection, project, level)
    collection.name = f"{project.engine.name}"
    return collection


def add_engine_to_collection(collection: Collection, project: Project, engine: Engine):
    packaged_engine = package_engine(engine.data)
    item = {
        "name": engine.name,
        "version": engine.version,
        "title": engine.title,
        "subtitle": engine.subtitle,
        "author": engine.author,
        "tags": [],
        "skin": collection.get_item("skins", engine.skin) if engine.skin else collection.get_default_item("skins"),
        "background": collection.get_item("backgrounds", engine.background)
        if engine.background
        else collection.get_default_item("backgrounds"),
        "effect": collection.get_item("effects", engine.effect)
        if engine.effect
        else collection.get_default_item("effects"),
        "particle": collection.get_item("particles", engine.particle)
        if engine.particle
        else collection.get_default_item("particles"),
        "thumbnail": load_resource(collection, engine.thumbnail, project.resources, BLANK_PNG),
        "playData": collection.add_asset(packaged_engine.play_data),
        "watchData": collection.add_asset(packaged_engine.watch_data),
        "previewData": collection.add_asset(packaged_engine.preview_data),
        "tutorialData": collection.add_asset(packaged_engine.tutorial_data),
        "rom": collection.add_asset(packaged_engine.rom),
        "configuration": collection.add_asset(packaged_engine.configuration),
    }
    collection.add_item("engines", engine.name, item)


def add_level_to_collection(collection: Collection, project: Project, level: Level):
    packaged_level_data = package_level_data(level.data)
    item = {
        "name": level.name,
        "version": level.version,
        "rating": level.rating,
        "title": level.title,
        "artists": level.artists,
        "author": level.author,
        "tags": [],
        "engine": collection.get_item("engines", project.engine.name),
        "useSkin": {"useDefault": True},
        "useBackground": {"useDefault": True},
        "useEffect": {"useDefault": True},
        "useParticle": {"useDefault": True},
        "cover": load_resource(collection, level.cover, project.resources, BLANK_PNG),
        "bgm": load_resource(collection, level.bgm, project.resources, BLANK_AUDIO),
        "data": collection.add_asset(packaged_level_data),
    }
    collection.add_item("levels", level.name, item)


def load_resource(collection: Collection, asset: Asset | None, base_path: Path, default: bytes) -> Srl:
    if asset is None:
        return collection.add_asset(default)
    if isinstance(asset, str) and not asset.startswith(("http://", "https://")):
        return collection.add_asset(base_path / asset)
    return collection.add_asset(asset)


def load_resources_files_to_collection(base_path: Path) -> Collection:
    collection = Collection()
    for path in base_path.rglob("*.scp"):
        collection.load_from_scp(path)
    collection.load_from_source(base_path)
    return collection
