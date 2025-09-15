from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from os import PathLike
from pathlib import Path
from typing import Any, NamedTuple, NotRequired, TypedDict

from sonolus.build.collection import Asset, load_asset
from sonolus.script.archetype import PlayArchetype, StandardArchetypeName, StandardImport
from sonolus.script.metadata import AnyText, Tag, as_localization_text


class ExportedLevel:
    """An exported Sonolus level."""

    def __init__(
        self,
        *,
        item: dict,
        cover: bytes,
        bgm: bytes,
        preview: bytes | None,
        data: bytes,
    ):
        self.item = item
        self.cover = cover
        self.bgm = bgm
        self.preview = preview
        self.data = data

    def write_to_dir(self, path: PathLike):
        """Write the exported level to a directory."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        (path / "item.json").write_text(json.dumps(self.item, ensure_ascii=False), encoding="utf-8")
        (path / "cover").write_bytes(self.cover)
        (path / "bgm").write_bytes(self.bgm)
        if self.preview is not None:
            (path / "preview").write_bytes(self.preview)
        (path / "data").write_bytes(self.data)


class Level:
    """A Sonolus level.

    Args:
        name: The name of the level.
        title: The title of the level.
        rating: The rating of the level.
        artists: The artists of the level.
        author: The author of the level.
        cover: The cover of the level.
        bgm: The background music of the level.
        data: The data of the level.
        use_skin: The skin to use, overriding the engine skin.
        use_background: The background to use, overriding the engine background.
        use_effect: The effect to use, overriding the engine effect.
        use_particle: The particle to use, overriding the engine particle.
        tags: The tags of the level.
        description: The description of the level.
        meta: Additional metadata of the level.
    """

    version = 1

    def __init__(
        self,
        *,
        name: str,
        title: AnyText | None = None,
        rating: int = 0,
        artists: AnyText = "Unknown",
        author: AnyText = "Unknown",
        cover: Asset | None = None,
        bgm: Asset | None = None,
        preview: Asset | None = None,
        data: LevelData,
        use_skin: str | None = None,
        use_background: str | None = None,
        use_effect: str | None = None,
        use_particle: str | None = None,
        tags: list[Tag] | None = None,
        description: AnyText | None = None,
        meta: Any = None,
    ) -> None:
        self.name = name
        self.title = as_localization_text(title or name)
        self.rating = rating
        self.artists = as_localization_text(artists)
        self.author = as_localization_text(author)
        self.cover = cover
        self.bgm = bgm
        self.preview = preview
        self.data = data
        self.use_skin = use_skin
        self.use_background = use_background
        self.use_effect = use_effect
        self.use_particle = use_particle
        self.tags = tags or []
        self.description = as_localization_text(description) if description is not None else None
        self.meta = meta

    def export(self, engine_name: str) -> ExportedLevel:
        """Export the level in a sonolus-pack compatible format.

        Args:
            engine_name: The name of the engine this level is for.

        Returns:
            The exported level.
        """
        from sonolus.build.level import package_level_data
        from sonolus.build.project import BLANK_AUDIO, BLANK_PNG

        item = {
            "version": self.version,
            "rating": self.rating,
            "engine": engine_name,
            "useSkin": {"useDefault": True} if self.use_skin is None else {"useDefault": False, "item": self.use_skin},
            "useBackground": {"useDefault": True}
            if self.use_background is None
            else {"useDefault": False, "item": self.use_background},
            "useEffect": {"useDefault": True}
            if self.use_effect is None
            else {"useDefault": False, "item": self.use_effect},
            "useParticle": {"useDefault": True}
            if self.use_particle is None
            else {"useDefault": False, "item": self.use_particle},
            "title": self.title,
            "artists": self.artists,
            "author": self.author,
            "tags": [tag.as_dict() for tag in self.tags],
        }
        if self.description is not None:
            item["description"] = self.description
        if self.meta is not None:
            item["meta"] = self.meta
        return ExportedLevel(
            item=item,
            cover=load_asset(self.cover) if self.cover is not None else BLANK_PNG,
            bgm=load_asset(self.bgm) if self.bgm is not None else BLANK_AUDIO,
            preview=load_asset(self.preview) if self.preview is not None else None,
            data=package_level_data(self.data),
        )


type EntityListArg = list[list[PlayArchetype] | PlayArchetype] | PlayArchetype


def flatten_entities(entities: EntityListArg) -> Iterator[PlayArchetype]:
    """Flatten a list of entities.

    Args:
        entities: The list of entities.

    Yields:
        The flattened entities.
    """
    if isinstance(entities, list):
        for entity in entities:
            yield from flatten_entities(entity)
    else:
        yield entities


class LevelData:
    """The data of a Sonolus level.

    Args:
        bgm_offset: The background music audio offset.
        entities: The entities of the level.
    """

    bgm_offset: float
    entities: list[PlayArchetype]

    def __init__(self, bgm_offset: float, entities: EntityListArg) -> None:
        self.bgm_offset = bgm_offset
        self.entities = [*flatten_entities(entities)]


class BpmChange(PlayArchetype):
    """The standard bpm change archetype."""

    name = StandardArchetypeName.BPM_CHANGE

    beat: StandardImport.BEAT
    bpm: StandardImport.BPM


class TimescaleChange(PlayArchetype):
    """The standard timescale change archetype."""

    name = StandardArchetypeName.TIMESCALE_CHANGE

    beat: StandardImport.BEAT
    timescale: StandardImport.TIMESCALE


class ExternalLevelDataDict(TypedDict):
    bgmOffset: float
    entities: list[ExternalEntityDataDict]


class ExternalEntityDataDict(TypedDict):
    name: NotRequired[str]
    archetype: str
    data: NotRequired[list[ExternalEntityDataValueDict]]


class ExternalEntityDataValueDict(TypedDict):
    name: str
    value: NotRequired[int | float]
    ref: NotRequired[str]


class ExternalLevelData(NamedTuple):
    """Level data parsed from an external source."""

    bgm_offset: float
    entities: list[ExternalEntityData]


class ExternalEntityData(NamedTuple):
    """Entity data parsed from an external source."""

    archetype: str
    data: dict[str, Any]


def parse_external_level_data(raw_data: ExternalLevelDataDict | str | bytes, /) -> ExternalLevelData:
    """Parse level data from an external source.

    If given a string, it is parsed as JSON. If given bytes, it is un-gzipped and then parsed as JSON.

    Args:
        raw_data: The raw level data to parse.

    Returns:
        The parsed level data.
    """
    if isinstance(raw_data, bytes):
        raw_data = gzip.decompress(raw_data).decode("utf-8")
    if isinstance(raw_data, str):
        raw_data = json.loads(raw_data)
    bgm_offset = raw_data["bgmOffset"]
    raw_entities = raw_data["entities"]
    entity_name_to_index = {e["name"]: i for i, e in enumerate(raw_entities) if "name" in e}
    entities = []
    for raw_entity in raw_entities:
        archetype = raw_entity["archetype"]
        data = {}
        for entry in raw_entity.get("data", []):
            if "value" in entry:
                data[entry["name"]] = entry["value"]
            elif "ref" in entry:
                data[entry["name"]] = entity_name_to_index.get(entry["ref"], 0)
        entities.append(ExternalEntityData(archetype=archetype, data=data))
    return ExternalLevelData(bgm_offset=bgm_offset, entities=entities)
