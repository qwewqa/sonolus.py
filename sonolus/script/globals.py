import inspect

from sonolus.backend.blocks import Block, PlayBlock, PreviewBlock, TutorialBlock, WatchBlock
from sonolus.backend.mode import Mode
from sonolus.script.internal.generic import validate_concrete_type
from sonolus.script.internal.value import Value


class GlobalInfo:
    def __init__(self, name: str, size: int, blocks: dict[Mode, Block], offset: int | None):
        self.name = name
        self.size = size
        self.blocks = blocks
        self.offset = offset


class GlobalField:
    def __init__(self, name: str, type_: type[Value], index: int, offset: int):
        self.name = name
        self.type = type_
        self.index = index
        self.offset = offset

    def __get__(self, instance, owner):
        from sonolus.script.internal.context import ctx

        info = owner._global_info_
        if not ctx():
            raise RuntimeError("Global field access outside of compilation")
        base = ctx().get_global_base(info)
        return self.type._from_place_(base.add_offset(self.offset))._get_()

    def __set__(self, instance, value):
        from sonolus.script.internal.context import ctx

        info = instance._global_info_
        if not ctx():
            raise RuntimeError("Global field access outside of compilation")
        base = ctx().get_global_base(info)
        target = self.type._from_place_(base.add_offset(self.offset))
        if self.type._is_value_type_():
            target._set_(value)
        else:
            target._copy_from_(value)


def create_global(cls: type, blocks: dict[Mode, Block], offset: int | None):
    if len(cls.__bases__) != 1:
        raise TypeError("GlobalProxy must not inherit from any class (other than object)")
    index = 0
    field_offset = 0
    for (
        name,
        annotation,
    ) in inspect.get_annotations(cls, eval_str=True).items():
        type_ = validate_concrete_type(annotation)
        setattr(cls, name, GlobalField(name, type_, index, field_offset))
        field_offset += type_._size_()
        index += 1
    cls._global_info_ = GlobalInfo(cls.__name__, field_offset, blocks, offset)
    return cls()


def play_runtime_environment[T](cls: type[T]) -> T:
    return create_global(cls, {Mode.Play: PlayBlock.RuntimeEnvironment}, None)


def watch_runtime_environment[T](cls: type[T]) -> T:
    return create_global(cls, {Mode.Watch: WatchBlock.RuntimeEnvironment}, None)


def tutorial_runtime_environment[T](cls: type[T]) -> T:
    return create_global(cls, {Mode.Tutorial: TutorialBlock.RuntimeEnvironment}, None)


def preview_runtime_environment[T](cls: type[T]) -> T:
    return create_global(cls, {Mode.Preview: PreviewBlock.RuntimeEnvironment}, None)


def play_runtime_update[T](cls: type[T]) -> T:
    return create_global(cls, {Mode.Play: PlayBlock.RuntimeUpdate}, None)


def watch_runtime_update[T](cls: type[T]) -> T:
    return create_global(cls, {Mode.Watch: WatchBlock.RuntimeUpdate}, None)


def tutorial_runtime_update[T](cls: type[T]) -> T:
    return create_global(cls, {Mode.Tutorial: TutorialBlock.RuntimeUpdate}, None)


def runtime_touch_array[T](cls: type[T]) -> T:
    return create_global(cls, {Mode.Play: PlayBlock.RuntimeTouchArray}, None)


def runtime_skin_transform[T](cls: type[T]) -> T:
    return create_global(
        cls,
        {
            Mode.Play: PlayBlock.RuntimeSkinTransform,
            Mode.Watch: WatchBlock.RuntimeSkinTransform,
            Mode.Tutorial: TutorialBlock.RuntimeSkinTransform,
        },
        None,
    )


def runtime_particle_transform[T](cls: type[T]) -> T:
    return create_global(
        cls,
        {
            Mode.Play: PlayBlock.RuntimeParticleTransform,
            Mode.Watch: WatchBlock.RuntimeParticleTransform,
            Mode.Tutorial: TutorialBlock.RuntimeParticleTransform,
        },
        None,
    )


def runtime_background[T](cls: type[T]) -> T:
    return create_global(
        cls,
        {
            Mode.Play: PlayBlock.RuntimeBackground,
            Mode.Watch: WatchBlock.RuntimeBackground,
            Mode.Tutorial: TutorialBlock.RuntimeBackground,
        },
        None,
    )


def runtime_ui[T](cls: type[T]) -> T:
    return create_global(
        cls,
        {
            Mode.Play: PlayBlock.RuntimeUI,
            Mode.Watch: WatchBlock.RuntimeUI,
            Mode.Tutorial: TutorialBlock.RuntimeUI,
            Mode.Preview: PreviewBlock.RuntimeUI,
        },
        None,
    )


def runtime_ui_configuration[T](cls: type[T]) -> T:
    return create_global(
        cls,
        {
            Mode.Play: PlayBlock.RuntimeUIConfiguration,
            Mode.Watch: WatchBlock.RuntimeUIConfiguration,
            Mode.Tutorial: TutorialBlock.RuntimeUIConfiguration,
            Mode.Preview: PreviewBlock.RuntimeUIConfiguration,
        },
        None,
    )


def level_memory[T](cls: type[T]) -> T:
    return create_global(cls, {Mode.Play: PlayBlock.LevelMemory, Mode.Watch: WatchBlock.LevelMemory}, None)


def level_data[T](cls: type[T]) -> T:
    return create_global(cls, {Mode.Play: PlayBlock.LevelData, Mode.Watch: WatchBlock.LevelData}, None)


def level_option[T](cls: type[T]) -> T:
    return create_global(cls, {Mode.Play: PlayBlock.LevelOption, Mode.Watch: WatchBlock.LevelOption}, None)


def level_bucket[T](cls: type[T]) -> T:
    return create_global(cls, {Mode.Play: PlayBlock.LevelBucket, Mode.Watch: WatchBlock.LevelBucket}, None)


def level_score[T](cls: type[T]) -> T:
    return create_global(cls, {Mode.Play: PlayBlock.LevelScore, Mode.Watch: WatchBlock.LevelScore}, None)


def level_life[T](cls: type[T]) -> T:
    return create_global(cls, {Mode.Play: PlayBlock.LevelLife, Mode.Watch: WatchBlock.LevelLife}, None)


def singleton[T](cls: type[T]) -> T:
    return create_global(cls, {}, None)


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
