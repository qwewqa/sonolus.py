from sonolus.build.engine import JsonValue, package_data
from sonolus.script.level import LevelData


def package_level_data(
    level_data: LevelData,
) -> bytes:
    return package_data(build_level_data(level_data))


def build_level_data(
    level_data: LevelData,
) -> JsonValue:
    level_refs = {entity: f"{i}_{entity.name}" for i, entity in enumerate(level_data.entities)}
    entities = []
    for i, entity in enumerate(level_data.entities):
        try:
            entries = entity._level_data_entries(level_refs)
        except ValueError as e:
            raise ValueError(f"Error in level entity {i} ('{entity.name}'): {e}") from e
        entities.append(
            {
                "name": level_refs[entity],
                "archetype": entity.name,
                "data": entries,
            }
        )
    return {
        "bgmOffset": level_data.bgm_offset,
        "entities": entities,
    }
