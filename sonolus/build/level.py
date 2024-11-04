from sonolus.build.engine import package_output
from sonolus.script.level import LevelData


def package_level_data(
    level_data: LevelData,
):
    return package_output(build_level_data(level_data))


def build_level_data(
    level_data: LevelData,
):
    return {
        "bgmOffset": level_data.bgm_offset,
        "entities": [
            {
                "archetype": entity.name,
                "data": entity._level_data_entries(),
            }
            for entity in level_data.entities
        ],
    }
