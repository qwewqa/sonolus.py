from sonolus.build.engine import package_output
from sonolus.script.level import LevelData


def package_level_data(
    level_data: LevelData,
):
    return package_output(build_level_data(level_data))


def build_level_data(
    level_data: LevelData,
):
    level_refs = {entity: f"{i}_{entity.name}" for i, entity in enumerate(level_data.entities)}
    return {
        "bgmOffset": level_data.bgm_offset,
        "entities": [
            {
                "name": level_refs[entity],
                "archetype": entity.name,
                "data": entity._level_data_entries(level_refs),
            }
            for entity in level_data.entities
        ],
    }
