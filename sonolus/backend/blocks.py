from enum import Enum


class BlockData(int):
    def __new__(cls, id_: int, readable: set[str], writable: set[str]):
        obj = int.__new__(cls, id_)
        obj.readable = readable
        obj.writable = writable
        return obj


class BlockEnum(BlockData, Enum):
    def __str__(self):
        return self.name


class TutorialBlock(BlockEnum):
    RuntimeEnvironment = (1000, {"preprocess", "navigate", "update"}, {"preprocess"})
    RuntimeUpdate = (1001, {"preprocess", "navigate", "update"}, {})
    RuntimeSkinTransform = (1002, {"preprocess", "navigate", "update"}, {"preprocess", "navigate", "update"})
    RuntimeParticleTransform = (1003, {"preprocess", "navigate", "update"}, {"preprocess", "navigate", "update"})
    RuntimeBackground = (1004, {"preprocess", "navigate", "update"}, {"preprocess", "navigate", "update"})
    RuntimeUI = (1005, {"preprocess", "navigate", "update"}, {"preprocess"})
    RuntimeUIConfiguration = (1006, {"preprocess", "navigate", "update"}, {"preprocess"})
    TutorialMemory = (2000, {"preprocess", "navigate", "update"}, {"preprocess", "navigate", "update"})
    TutorialData = (2001, {"preprocess", "navigate", "update"}, {"preprocess"})
    TutorialInstruction = (2002, {"preprocess", "navigate", "update"}, {"preprocess", "navigate", "update"})
    EngineRom = (3000, {"preprocess", "navigate", "update"}, {})
    TemporaryMemory = (10000, {"preprocess", "navigate", "update"}, {"preprocess", "navigate", "update"})


class PlayBlock(BlockEnum):
    RuntimeEnvironment = (
        1000,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess"},
    )
    RuntimeUpdate = (
        1001,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {},
    )
    RuntimeTouchArray = (
        1002,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {},
    )
    RuntimeSkinTransform = (
        1003,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess", "updateSequential", "touch"},
    )
    RuntimeParticleTransform = (
        1004,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess", "updateSequential", "touch"},
    )
    RuntimeBackground = (
        1005,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess", "updateSequential", "touch"},
    )
    RuntimeUI = (
        1006,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess"},
    )
    RuntimeUIConfiguration = (
        1007,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess"},
    )
    LevelMemory = (
        2000,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess", "updateSequential", "touch"},
    )
    LevelData = (
        2001,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess"},
    )
    LevelOption = (
        2002,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {},
    )
    LevelBucket = (
        2003,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess"},
    )
    LevelScore = (
        2004,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess"},
    )
    LevelLife = (
        2005,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess"},
    )
    EngineRom = (
        3000,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {},
    )
    EntityMemory = (
        4000,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
    )
    EntityData = (
        4001,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess"},
    )
    EntitySharedMemory = (
        4002,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess", "updateSequential", "touch"},
    )
    EntityInfo = (
        4003,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {},
    )
    EntityDespawn = (
        4004,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
    )
    EntityInput = (
        4005,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
    )
    EntityDataArray = (
        4101,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess"},
    )
    EntitySharedMemoryArray = (
        4102,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess", "updateSequential", "touch"},
    )
    EntityInfoArray = (
        4103,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {},
    )
    ArchetypeLife = (
        5000,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {"preprocess"},
    )
    TemporaryMemory = (
        10000,
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
        {
            "preprocess",
            "spawnOrder",
            "shouldSpawn",
            "initialize",
            "updateSequential",
            "touch",
            "updateParallel",
            "terminate",
        },
    )


class PreviewBlock(BlockEnum):
    RuntimeEnvironment = (1000, {"preprocess", "render"}, {"preprocess"})
    RuntimeCanvas = (1001, {"preprocess", "render"}, {"preprocess"})
    RuntimeSkinTransform = (1002, {"preprocess", "render"}, {"preprocess"})
    RuntimeUI = (1003, {"preprocess", "render"}, {"preprocess"})
    RuntimeUIConfiguration = (1004, {"preprocess", "render"}, {"preprocess"})
    PreviewData = (2000, {"preprocess", "render"}, {"preprocess"})
    PreviewOption = (2001, {"preprocess", "render"}, {})
    EngineRom = (3000, {"preprocess", "render"}, {})
    EntityData = (4000, {"preprocess", "render"}, {"preprocess"})
    EntitySharedMemory = (4001, {"preprocess", "render"}, {"preprocess"})
    EntityInfo = (4002, {"preprocess", "render"}, {})
    EntityDataArray = (4100, {"preprocess", "render"}, {"preprocess"})
    EntitySharedMemoryArray = (4101, {"preprocess", "render"}, {"preprocess"})
    EntityInfoArray = (4102, {"preprocess", "render"}, {})
    TemporaryMemory = (10000, {"preprocess", "render"}, {"preprocess", "render"})


class WatchBlock(BlockEnum):
    RuntimeEnvironment = (
        1000,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {"preprocess"},
    )
    RuntimeUpdate = (
        1001,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {},
    )
    RuntimeSkinTransform = (
        1002,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {"preprocess", "updateSequential"},
    )
    RuntimeParticleTransform = (
        1003,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {"preprocess", "updateSequential"},
    )
    RuntimeBackground = (
        1004,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {"preprocess", "updateSequential"},
    )
    RuntimeUI = (
        1005,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {"preprocess"},
    )
    RuntimeUIConfiguration = (
        1006,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {"preprocess"},
    )
    LevelMemory = (
        2000,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {"preprocess", "updateSequential"},
    )
    LevelData = (
        2001,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {"preprocess"},
    )
    LevelOption = (
        2002,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {},
    )
    LevelBucket = (
        2003,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {"preprocess"},
    )
    LevelScore = (
        2004,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {"preprocess"},
    )
    LevelLife = (
        2005,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {"preprocess"},
    )
    EngineRom = (
        3000,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {},
    )
    EntityMemory = (
        4000,
        {"preprocess", "spawnTime", "despawnTime", "initialize", "updateSequential", "updateParallel", "terminate"},
        {"preprocess", "spawnTime", "despawnTime", "initialize", "updateSequential", "updateParallel", "terminate"},
    )
    EntityData = (
        4001,
        {"preprocess", "spawnTime", "despawnTime", "initialize", "updateSequential", "updateParallel", "terminate"},
        {"preprocess"},
    )
    EntitySharedMemory = (
        4002,
        {"preprocess", "spawnTime", "despawnTime", "initialize", "updateSequential", "updateParallel", "terminate"},
        {"preprocess", "updateSequential"},
    )
    EntityInfo = (
        4003,
        {"preprocess", "spawnTime", "despawnTime", "initialize", "updateSequential", "updateParallel", "terminate"},
        {},
    )
    EntityInput = (
        4004,
        {"preprocess", "spawnTime", "despawnTime", "initialize", "updateSequential", "updateParallel", "terminate"},
        {"preprocess"},
    )
    EntityDataArray = (
        4101,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {"preprocess"},
    )
    EntitySharedMemoryArray = (
        4102,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {"preprocess", "updateSequential"},
    )
    EntityInfoArray = (
        4103,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {},
    )
    ArchetypeLife = (
        5000,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {"preprocess"},
    )
    TemporaryMemory = (
        10000,
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
        {
            "preprocess",
            "spawnTime",
            "despawnTime",
            "initialize",
            "updateSequential",
            "updateParallel",
            "terminate",
            "updateSpawn",
        },
    )


type Block = TutorialBlock | PlayBlock | PreviewBlock | WatchBlock
