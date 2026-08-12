from __future__ import annotations

from inspect import signature

import pytest

from sonolus.script.archetype import AnyArchetype, EntityRef
from sonolus.script.bucket import BucketSprite, bucket, bucket_sprite
from sonolus.script.engine import PlayMode, PreviewMode, WatchMode
from sonolus.script.quad import QuadLike
from sonolus.script.runtime import LevelLifeData, LevelScoreData, PreviewRuntimeCanvas, canvas, level_life, level_score


@pytest.mark.parametrize(
    "api",
    [
        bucket,
        bucket_sprite,
        PlayMode,
        WatchMode,
        PreviewMode,
        canvas,
        level_score,
        level_life,
        EntityRef,
        EntityRef.with_archetype,
        EntityRef.get_as,
    ],
)
def test_public_signatures_do_not_expose_private_types(api):
    assert "_BaseArchetype" not in str(signature(api))
    assert "_BucketSprite" not in str(signature(api))
    assert "_PreviewRuntimeCanvas" not in str(signature(api))
    assert "_LevelScore" not in str(signature(api))
    assert "_LevelLife" not in str(signature(api))


def test_public_generic_bounds_do_not_expose_private_types():
    assert EntityRef.__type_params__[0].__bound__ == AnyArchetype
    assert EntityRef.with_archetype.__type_params__[0].__bound__ == AnyArchetype
    assert EntityRef.get_as.__type_params__[0].__bound__ == AnyArchetype


@pytest.mark.parametrize(
    ("api", "public_type"),
    [
        (bucket, "BucketSprite"),
        (bucket_sprite, "BucketSprite"),
        (PlayMode, "PlayArchetype"),
        (WatchMode, "WatchArchetype"),
        (PreviewMode, "PreviewArchetype"),
        (canvas, "PreviewRuntimeCanvas"),
        (level_score, "LevelScoreData"),
        (level_life, "LevelLifeData"),
    ],
)
def test_public_signatures_name_the_supported_type(api, public_type):
    assert public_type in str(signature(api))


@pytest.mark.parametrize("public_type", [BucketSprite, PreviewRuntimeCanvas, LevelScoreData, LevelLifeData])
def test_public_annotation_names_are_types(public_type):
    assert isinstance(public_type, type)


def test_quad_like_is_a_public_protocol():
    assert QuadLike.__name__ == "QuadLike"
    assert QuadLike._is_protocol
