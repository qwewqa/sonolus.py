from typing import Annotated, Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sonolus.build.level import build_level_data
from sonolus.script.archetype import EntityRef, PlayArchetype, imported
from sonolus.script.array import Array
from sonolus.script.debug import assert_true, debug_log
from sonolus.script.level import LevelData
from sonolus.script.num import Num
from sonolus.script.record import Record
from sonolus.script.values import copy, zeros
from tests.script.conftest import run_and_validate

# run_and_validate's closure-from-ROM leg interns closure values into engine ROM, which holds 32-bit floats
# and rejects magnitudes above f32 max, so the strategies stay within the range engine data can hold.
f32_range_floats = st.floats(
    allow_nan=False, allow_infinity=False, min_value=-3.4028234663852886e38, max_value=3.4028234663852886e38
)


class Simple(Record):
    value: float


class Generic[T](Record):
    value: T

    def __add__(self, other):
        return Generic(self.value + other.value)

    def __isub__(self, other):
        self.value = 123
        return self

    @property
    def prop(self):
        debug_log(1)
        return self.value

    @prop.setter
    def prop(self, value):
        debug_log(2)
        self.value = value

    def __iadd__(self, other):
        self.value += other.value
        debug_log(3)
        return self


class Pair[T, U](Record):
    first: T
    second: U


class ConcreteCompound(Record):
    a: Pair[Num, Num]
    b: Pair[Num, Num]


def test_record_field_type_spec_is_normalized():
    class Tagged(Record):
        value: Annotated[int, "tag"]

    assert Tagged._fields_[0].type is Num


@given(a=f32_range_floats)
def test_simple_record(a):
    def fn():
        r = Simple(a)
        assert_true(r.value == a)
        return 1

    assert run_and_validate(fn) == 1


@given(a=f32_range_floats)
def test_generic_record_inference(a):
    def fn():
        r = Generic(a)
        assert_true(r.value == a)
        return 1

    assert run_and_validate(fn) == 1


@given(a=f32_range_floats)
def test_generic_record_explicit(a):
    def fn():
        r = Generic[Num](a)
        assert_true(r.value == a)
        return 1

    assert run_and_validate(fn) == 1


def test_concrete_compound_record():
    def fn():
        r = ConcreteCompound(Pair(1, 2), Pair(3, 4))
        r @= ConcreteCompound(Pair(5, 6), Pair(7, 8))
        return r

    assert run_and_validate(fn) == ConcreteCompound(Pair(5, 6), Pair(7, 8))


def test_record_rejects_wrong_type():
    with pytest.raises(TypeError):
        Simple(Simple(1))


def test_concrete_generic_record_rejects_wrong_type():
    with pytest.raises(TypeError):
        Generic[Simple](1)


def test_nested_generic():
    def fn():
        r = Generic[Generic[Num]](Generic[Num](1))
        assert_true(r.value.value == 1)
        r2 = Generic[Generic[Num]](Generic(2))
        assert_true(r2.value.value == 2)
        return 1

    assert run_and_validate(fn) == 1


def test_value_record_members_are_independent():
    def fn():
        v = 1
        r = Generic[Num](v)
        assert_true(r.value == v)
        r.value = 2
        assert_true(r.value == 2)
        assert_true(v == 1)
        return 1

    assert run_and_validate(fn) == 1


def test_reference_record_members_are_shared():
    def fn():
        inner = Generic[Num](1)
        outer = Generic(inner)
        assert_true(outer.value.value == 1)
        inner.value = 2
        assert_true(outer.value.value == 2)
        outer.value.value = 3
        assert_true(inner.value == 3)
        return 1

    assert run_and_validate(fn) == 1


def test_automatic_record_copy_assign():
    def fn():
        inner = Generic(1)
        other = Generic(2)
        outer = Generic(inner)
        outer.value = other
        assert_true(outer.value.value == 2)
        assert_true(inner.value == 2)
        return 1

    assert run_and_validate(fn) == 1


def test_record_operator_overloading():
    def fn():
        r1 = Generic(1)
        r2 = Generic(2)
        r = r1 + r2
        assert_true(r == Generic(3))
        return 1

    assert run_and_validate(fn) == 1


def test_record_inplace_operator_generation():
    def fn():
        r = Generic(1)
        r_ref = r
        r += Generic(2)
        assert_true(r == Generic(3))
        assert_true(r_ref == Generic(3))
        return 1

    assert run_and_validate(fn) == 1


def test_record_explicit_inplace_operator():
    def fn():
        r = Generic(1)
        r_ref = r
        r -= Generic(2)
        assert_true(r == Generic(123))
        assert_true(r_ref == Generic(123))
        return 1

    assert run_and_validate(fn) == 1


def test_record_equality():
    def fn():
        r1 = Pair(1, 2)
        r2 = Pair(1, 2)
        r3 = Pair(3, 4)
        r4 = Pair(Simple(1), Simple(2))
        r5 = Pair(Simple(1), Simple(2))
        r6 = Pair(Simple(3), Simple(4))

        assert_true(r1 == r2)
        assert_true(r1 != r3)
        assert_true(r4 == r5)
        assert_true(r4 != r6)

        return 1

    assert run_and_validate(fn) == 1


@given(
    a=st.integers(min_value=0, max_value=20),
    b=st.integers(min_value=0, max_value=20),
    c=st.integers(min_value=0, max_value=20),
    d=st.integers(min_value=0, max_value=20),
    e=st.integers(min_value=0, max_value=20),
    f=st.integers(min_value=0, max_value=20),
)
def test_record_modification_in_loop(a, b, c, d, e, f):
    # This serves as a test that optimizations don't break even for complex control flow
    def fn():
        r = Pair(a, b)
        while r.first < c:
            if r.first < d:
                for i in range(r.first, r.second):
                    if i == f:
                        break
                    r.second += i
                r @= Pair(a + 1, b - 1)
                break
            if r.first == e:
                r.first += 3
                continue
            r.first += 1
            r.second += 5
        return r

    run_and_validate(fn)


def test_record_property_getter():
    def fn():
        r = Generic(1)
        return r.prop

    assert run_and_validate(fn) == 1


def test_record_property_setter():
    def fn():
        r = Generic(1)
        r.prop = 2
        return r.value

    assert run_and_validate(fn) == 2


def test_record_augmented_property_setter():
    def fn():
        r = Generic(1)
        r.prop += 2
        return r.value

    assert run_and_validate(fn) == 3


def test_record_augmented_property_setter_with_explicit_implementation():
    def fn():
        r = Generic(Generic(1))
        r.prop += Generic(2)
        return r.value.value

    assert run_and_validate(fn) == 3


def test_type_of_generic_record():
    def fn():
        r = Generic(Pair(1, 2))
        t = type(r)
        return t(Pair(3, 4))

    assert run_and_validate(fn) == Generic(Pair(3, 4))


def test_record_rejects_unparameterized_generic_field():
    with pytest.raises(TypeError, match="Are there missing type arguments"):

        class Bad(Record):
            ref: EntityRef
            lane: int


def test_record_accepts_parameterized_generic_field():
    class Good(Record):
        ref: EntityRef[Any]
        lane: int

    assert [(field.name, field.offset) for field in Good._fields_] == [("ref", 0), ("lane", 1)]
    assert Good._size_() == 2

    def fn():
        arr = zeros(Array[Good, 1])
        arr[0].lane = 11
        return arr[0].ref.index

    assert run_and_validate(fn) == 0


def test_record_allows_field_using_own_type_params():
    class Outer[T](Record):
        inner: Pair[T, T]
        extra: int

    assert [(field.name, field.offset) for field in Outer[Num]._fields_] == [("inner", 0), ("extra", 2)]
    assert Outer[Num]._size_() == 3

    def fn():
        arr = zeros(Array[Outer[Num], 1])
        arr[0].extra = 11
        return arr[0].inner.first + arr[0].inner.second

    assert run_and_validate(fn) == 0


class RefTarget(PlayArchetype):
    name = "RefTarget"

    value: int = imported()


class RefHolder(PlayArchetype):
    name = "RefHolder"

    target: EntityRef[RefTarget] = imported()


def test_entity_ref_with_archetype_preserves_level_data_ref():
    target = RefTarget(value=1)
    other = RefTarget(value=2)
    holder = RefHolder(target=target.ref().with_archetype(RefTarget))

    level_data = LevelData(bgm_offset=0, entities=[target, other, holder])

    assert build_level_data(level_data)["entities"][2] == {
        "name": "2_RefHolder",
        "archetype": "RefHolder",
        "data": [{"name": "target", "ref": "0_RefTarget"}],
    }


def test_entity_ref_with_archetype_preserves_level_data_identity():
    target = RefTarget(value=1)
    other = RefTarget(value=2)

    assert target.ref().with_archetype(RefTarget) == target.ref()
    assert target.ref().with_archetype(RefTarget) == target.ref().with_archetype(RefTarget)
    assert target.ref().with_archetype(RefTarget) != other.ref().with_archetype(RefTarget)
    # Every reference still holds a placeholder index while building level data, so `!=` has to follow `__eq__`
    # rather than comparing fields, or both comparisons report false at once.
    assert not (target.ref().with_archetype(RefTarget) != target.ref())  # noqa: SIM202
    assert hash(target.ref().with_archetype(RefTarget)) == hash(target.ref())
    assert hash(target.ref().with_archetype(RefTarget)) != hash(other.ref().with_archetype(RefTarget))


def test_entity_ref_copy_preserves_level_data_ref():
    class ArrayHolder(PlayArchetype):
        name = "ArrayHolder"

        targets: Array[EntityRef[RefTarget], 2] = imported()

    target = RefTarget(value=1)
    other = RefTarget(value=2)
    # Array copies each element, which is the path that drops the reference if _copy_ doesn't carry it.
    holder = ArrayHolder(targets=Array(target.ref(), other.ref()))

    level_data = LevelData(bgm_offset=0, entities=[target, other, holder])

    assert build_level_data(level_data)["entities"][2]["data"] == [
        {"name": "targets[0]", "ref": "0_RefTarget"},
        {"name": "targets[1]", "ref": "1_RefTarget"},
    ]


def test_entity_ref_reassignment_clears_level_data_ref():
    target = RefTarget(value=1)
    holder = RefHolder(target=target.ref())
    # Reassigning to a plain index reference must not leave the previous entity attached, since _to_list_ would
    # prefer it over the index just assigned.
    holder.target = EntityRef[RefTarget](index=5)

    level_data = LevelData(bgm_offset=0, entities=[target, holder])

    assert build_level_data(level_data)["entities"][1]["data"] == [{"name": "target", "value": 5}]


def test_entity_ref_copy_value_preserves_level_data_ref():
    target = RefTarget(value=1)
    holder = RefHolder(target=copy(target.ref()))

    level_data = LevelData(bgm_offset=0, entities=[target, holder])

    assert build_level_data(level_data)["entities"][1]["data"] == [{"name": "target", "ref": "0_RefTarget"}]


def test_entity_ref_with_archetype_in_compiled_function():
    def fn():
        ref = EntityRef[Any](index=3)
        return ref.with_archetype(RefTarget).index

    assert run_and_validate(fn) == 3


def test_entity_ref_with_archetype_on_storage_backed_ref():
    class Holder(Record):
        ref: EntityRef[RefTarget]

    def fn():
        arr = zeros(Array[Holder, 2])
        arr[0].ref.index = 7
        return arr[0].ref.with_archetype(RefTarget).index

    assert run_and_validate(fn) == 7
