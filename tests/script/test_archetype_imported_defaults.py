"""Tests for `imported(default=...)`: which defaults a field admits, and what each one puts in the import table.

An import default stands in for a value the level does not supply, and the level supplies one value per flat key
of the field's type. A field whose type flattens into several keys therefore takes a default of a matching type
rather than a single number, and a default of any other arity is rejected before the archetype is built.
"""

import pytest

from sonolus.script.archetype import ImportInfo, PlayArchetype, imported
from sonolus.script.array import Array
from sonolus.script.containers import Pair
from sonolus.script.vec import Vec2


def test_a_scalar_default_fills_the_one_key_a_scalar_field_imports():
    class ScalarDefault(PlayArchetype):
        beat: float = imported(default=7.0)

    ScalarDefault._init_fields()

    assert ScalarDefault._imported_keys_ == {"beat": ImportInfo(index=0, default=7.0)}


def test_a_record_default_fills_one_key_per_flat_slot():
    class RecordDefault(PlayArchetype):
        pos: Vec2 = imported(default=Vec2(1.0, 2.0))

    RecordDefault._init_fields()

    assert RecordDefault._imported_keys_ == {
        "pos.x": ImportInfo(index=0, default=1.0),
        "pos.y": ImportInfo(index=1, default=2.0),
    }


def test_a_pair_default_fills_one_key_per_flat_slot():
    class PairDefault(PlayArchetype):
        span: Pair[float, float] = imported(default=Pair(3.0, 4.0))

    PairDefault._init_fields()

    assert PairDefault._imported_keys_ == {
        "span.first": ImportInfo(index=0, default=3.0),
        "span.second": ImportInfo(index=1, default=4.0),
    }


def test_an_array_default_fills_one_key_per_flat_slot():
    class ArrayDefault(PlayArchetype):
        offsets: Array[float, 2] = imported(default=Array[float, 2](5.0, 6.0))

    ArrayDefault._init_fields()

    assert ArrayDefault._imported_keys_ == {
        "offsets[0]": ImportInfo(index=0, default=5.0),
        "offsets[1]": ImportInfo(index=1, default=6.0),
    }


def test_a_default_that_is_short_of_the_imported_keys_is_rejected():
    class TooFewDefaults(PlayArchetype):
        pos: Vec2 = imported(default=1.0)

    with pytest.raises(TypeError, match="Field 'pos' of TooFewDefaults imports 2 values, but its default has 1"):
        TooFewDefaults._init_fields()


def test_a_default_with_more_values_than_the_imported_keys_is_rejected():
    class TooManyDefaults(PlayArchetype):
        beat: float = imported(default=Vec2(1.0, 2.0))

    with pytest.raises(TypeError, match="Field 'beat' of TooManyDefaults imports 1 value, but its default has 2"):
        TooManyDefaults._init_fields()


def test_a_mismatched_default_on_an_inherited_field_names_the_archetype_that_declared_it():
    class DefaultBase(PlayArchetype):
        pos: Vec2 = imported(default=1.0)

    class DefaultSub(DefaultBase):
        beat: float = imported()

    with pytest.raises(TypeError, match="Field 'pos' of DefaultBase imports 2 values, but its default has 1"):
        DefaultSub._init_fields()
