"""Tests that `replay_fallback_option_names` is read as a sequence of names rather than as characters.

The attribute is a bare class attribute with no signature to annotate, and a one-element tuple written without
its trailing comma is a plain string, which is itself a sequence. Nothing downstream rejects a one-character
option name, so the mistake has to be caught here or not at all.
"""

import pytest

from sonolus.build.engine import build_engine_configuration
from sonolus.script.options import options, toggle_option
from sonolus.script.ui import UiConfig


@options
class _OneFallbackName:
    replay_fallback_option_names = ("legacy_option",)
    toggle: bool = toggle_option(name="Toggle", default=True)


@options
class _FallbackNamesAsAString:
    replay_fallback_option_names = "legacy_option"
    toggle: bool = toggle_option(name="Toggle", default=True)


@options
class _FallbackNamesAsAnEmptyString:
    replay_fallback_option_names = ""
    toggle: bool = toggle_option(name="Toggle", default=True)


def test_a_sequence_of_fallback_names_is_carried_through():
    result = build_engine_configuration(_OneFallbackName, UiConfig())

    assert result["replayFallbackOptionNames"] == ["legacy_option"]


def test_a_bare_string_of_fallback_names_is_rejected():
    with pytest.raises(TypeError, match="Expected a sequence of option names, got 'legacy_option'"):
        build_engine_configuration(_FallbackNamesAsAString, UiConfig())


def test_an_empty_bare_string_of_fallback_names_is_rejected():
    with pytest.raises(TypeError, match="Expected a sequence of option names, got ''"):
        build_engine_configuration(_FallbackNamesAsAnEmptyString, UiConfig())


@pytest.mark.parametrize("value", [{"legacy_option"}, {"legacy_option": 1}, (name for name in ["legacy_option"])])
def test_an_unordered_or_one_shot_sequence_of_fallback_names_is_rejected(value):
    options_with_invalid_fallback_names = type(
        "OptionsWithInvalidFallbackNames", (), {"_options_": [], "replay_fallback_option_names": value}
    )

    with pytest.raises(TypeError, match="Expected a sequence of option names"):
        build_engine_configuration(options_with_invalid_fallback_names, UiConfig())


def test_a_sequence_with_a_non_string_fallback_name_is_rejected():
    options_with_invalid_fallback_names = type(
        "OptionsWithInvalidFallbackNames", (), {"_options_": [], "replay_fallback_option_names": ("legacy_option", 1)}
    )

    with pytest.raises(TypeError, match="Expected a sequence of string option names"):
        build_engine_configuration(options_with_invalid_fallback_names, UiConfig())
