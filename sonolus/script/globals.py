import inspect
from typing import dataclass_transform

from sonolus.backend.blocks import Block, PlayBlock, PreviewBlock, TutorialBlock, WatchBlock
from sonolus.backend.mode import Mode
from sonolus.script.internal.descriptor import SonolusDescriptor
from sonolus.script.internal.generic import validate_concrete_type
from sonolus.script.internal.simulation_context import sim_ctx
from sonolus.script.internal.value import Value


class _GlobalInfo:
    def __init__(self, name: str, size: int, blocks: dict[Mode, Block], offset: int | None):
        self.name = name
        self.size = size
        self.blocks = blocks
        self.offset = offset


class _GlobalField(SonolusDescriptor):
    def __init__(self, name: str, type_: type[Value], index: int, offset: int):
        self.name = name
        self.type = type_
        self.index = index
        self.offset = offset

    def __get__(self, instance, owner):
        if instance is None:
            return self

        if sim_ctx():
            from sonolus.script.values import zeros

            return sim_ctx().get_or_put_value((instance, self), lambda: zeros(self.type))

        from sonolus.script.internal.context import ctx

        info = owner._global_info_
        if not ctx():
            raise RuntimeError("Global field access outside of compilation")
        base = ctx().get_global_base(info)
        return self.type._from_place_(base.add_offset(self.offset))._get_()

    def __set__(self, instance, value):
        from sonolus.script.internal.context import ctx

        if sim_ctx():
            from sonolus.script.values import zeros

            sim_ctx().set_or_put_value((instance, self), lambda: zeros(self.type), value)
            return

        info = instance._global_info_
        if not ctx():
            raise RuntimeError("Global field access outside of compilation")
        base = ctx().get_global_base(info)
        target = self.type._from_place_(base.add_offset(self.offset))
        if self.type._is_value_type_():
            target._set_(value)
        else:
            target._copy_from_(value)


class _GlobalPlaceholder:
    def __init__(self, type_: type[Value], blocks: dict[Mode, Block], offset: int | None):
        self.type = type_
        self.blocks = blocks
        self.offset = offset
        self.size = type_._size_()

    def get(self):
        from sonolus.script.internal.context import ctx

        if not ctx():
            raise RuntimeError("Global access outside of compilation")
        base = ctx().get_global_base(self)
        return self.type._from_place_(base)

    def _get_sim_replacement_(self):
        from sonolus.script.values import zeros

        return sim_ctx().get_or_put_value(self, lambda: zeros(self.type))


def _create_global(cls: type, blocks: dict[Mode, Block], offset: int | None):
    if issubclass(cls, Value):
        cls = validate_concrete_type(cls)
        return _GlobalPlaceholder(cls, blocks, offset)
    if len(cls.__bases__) != 1:
        raise TypeError("Expected a class with no bases or a Value subclass")
    field_offset = 0
    for i, (
        name,
        annotation,
    ) in enumerate(inspect.get_annotations(cls, eval_str=True).items()):
        type_ = validate_concrete_type(annotation)
        setattr(cls, name, _GlobalField(name, type_, i, field_offset))
        field_offset += type_._size_()
    cls._global_info_ = _GlobalInfo(cls.__name__, field_offset, blocks, offset)
    cls._is_comptime_value_ = True
    return cls()


@dataclass_transform()
def _play_runtime_environment[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.PLAY: PlayBlock.RuntimeEnvironment}, 0)


@dataclass_transform()
def _watch_runtime_environment[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.WATCH: WatchBlock.RuntimeEnvironment}, 0)


@dataclass_transform()
def _tutorial_runtime_environment[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.TUTORIAL: TutorialBlock.RuntimeEnvironment}, 0)


@dataclass_transform()
def _preview_runtime_environment[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.PREVIEW: PreviewBlock.RuntimeEnvironment}, 0)


@dataclass_transform()
def _play_runtime_update[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.PLAY: PlayBlock.RuntimeUpdate}, 0)


@dataclass_transform()
def _watch_runtime_update[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.WATCH: WatchBlock.RuntimeUpdate}, 0)


@dataclass_transform()
def _preview_runtime_canvas[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.PREVIEW: PreviewBlock.RuntimeCanvas}, 0)


@dataclass_transform()
def _tutorial_runtime_update[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.TUTORIAL: TutorialBlock.RuntimeUpdate}, 0)


@dataclass_transform()
def _runtime_touch_array[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.PLAY: PlayBlock.RuntimeTouchArray}, 0)


@dataclass_transform()
def _runtime_skin_transform[T](cls: type[T]) -> T:
    return _create_global(
        cls,
        {
            Mode.PLAY: PlayBlock.RuntimeSkinTransform,
            Mode.WATCH: WatchBlock.RuntimeSkinTransform,
            Mode.PREVIEW: PreviewBlock.RuntimeSkinTransform,
            Mode.TUTORIAL: TutorialBlock.RuntimeSkinTransform,
        },
        0,
    )


@dataclass_transform()
def _runtime_particle_transform[T](cls: type[T]) -> T:
    return _create_global(
        cls,
        {
            Mode.PLAY: PlayBlock.RuntimeParticleTransform,
            Mode.WATCH: WatchBlock.RuntimeParticleTransform,
            Mode.TUTORIAL: TutorialBlock.RuntimeParticleTransform,
        },
        0,
    )


@dataclass_transform()
def _runtime_background[T](cls: type[T]) -> T:
    return _create_global(
        cls,
        {
            Mode.PLAY: PlayBlock.RuntimeBackground,
            Mode.WATCH: WatchBlock.RuntimeBackground,
            Mode.TUTORIAL: TutorialBlock.RuntimeBackground,
        },
        0,
    )


@dataclass_transform()
def _play_runtime_ui[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.PLAY: PlayBlock.RuntimeUI}, 0)


@dataclass_transform()
def _watch_runtime_ui[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.WATCH: WatchBlock.RuntimeUI}, 0)


@dataclass_transform()
def _tutorial_runtime_ui[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.TUTORIAL: TutorialBlock.RuntimeUI}, 0)


@dataclass_transform()
def _preview_runtime_ui[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.PREVIEW: PreviewBlock.RuntimeUI}, 0)


@dataclass_transform()
def _play_runtime_ui_configuration[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.PLAY: PlayBlock.RuntimeUIConfiguration}, 0)


@dataclass_transform()
def _watch_runtime_ui_configuration[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.WATCH: WatchBlock.RuntimeUIConfiguration}, 0)


@dataclass_transform()
def _tutorial_runtime_ui_configuration[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.TUTORIAL: TutorialBlock.RuntimeUIConfiguration}, 0)


@dataclass_transform()
def _preview_runtime_ui_configuration[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.PREVIEW: PreviewBlock.RuntimeUIConfiguration}, 0)


@dataclass_transform()
def _tutorial_instruction[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.TUTORIAL: TutorialBlock.TutorialInstruction}, 0)


@dataclass_transform()
def level_memory[T](cls: type[T]) -> T:
    """Define level memory.

    Level memory may be modified during gameplay in sequential callbacks (`preprocess`, `update_sequential`, `touch`).

    Usage:
        ```python
        @level_memory
        class LevelMemory:
            variable: int

        variable = level_data(Array[int, 10])

        def f():
            LevelMemory.variable = 1
            variable[0] = 2
        ```
    """
    return _create_global(
        cls,
        {
            Mode.PLAY: PlayBlock.LevelMemory,
            Mode.WATCH: WatchBlock.LevelMemory,
            Mode.TUTORIAL: TutorialBlock.TutorialMemory,
        },
        None,
    )


@dataclass_transform()
def level_data[T](cls: type[T]) -> T:
    """Define level data.

    Level data may only be modified during preprocessing.

    Usage:
        ```python
        @level_data
        class LevelData:
            variable: int

        variable = level_data(Array[int, 10])

        def f():
            LevelData.variable = 1
            variable[0] = 2
        ```
    """
    return _create_global(
        cls,
        {
            Mode.PLAY: PlayBlock.LevelData,
            Mode.WATCH: WatchBlock.LevelData,
            Mode.PREVIEW: PreviewBlock.PreviewData,
            Mode.TUTORIAL: TutorialBlock.TutorialData,
        },
        None,
    )


# level_option is handled by the options decorator
# level_bucket is handled by the bucket decorator


@dataclass_transform()
def _level_score[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.PLAY: PlayBlock.LevelScore, Mode.WATCH: WatchBlock.LevelScore}, 0)


@dataclass_transform()
def _level_life[T](cls: type[T]) -> T:
    return _create_global(cls, {Mode.PLAY: PlayBlock.LevelLife, Mode.WATCH: WatchBlock.LevelLife}, 0)


# engine_rom is handled by the compiler
# entity memory is handled by the archetype
# entity data is handled by the archetype
# entity shared memory is handled by the archetype
# entity info is handled by the archetype
# entity despawn is handled by the archetype
# entity input is handled by the archetype
# entity data array is handled by the archetype
# entity shared memory array is handled by the archetype
# entity info array is handled by the archetype
# archetype life is handled by the archetype
# temporary memory is handled by the compiler
