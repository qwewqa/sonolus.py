import pytest

from sonolus.script.bucket import bucket
from sonolus.script.engine import Engine
from sonolus.script.instruction import Instruction, StandardInstruction, instruction, instructions
from sonolus.script.level import Level, LevelData
from sonolus.script.metadata import Tag, as_localization_text, encode_localization_text
from sonolus.script.options import OptionCategory, select_option, slider_option, toggle_option
from sonolus.script.text import StandardText

LOCALIZED_GREETING = {"en": "Hello World", "zhs": "你好世界"}
ENCODED_GREETING = '##LOCALIZE:{"en":"Hello World","zhs":"你好世界"}'


@pytest.mark.parametrize(
    ("text", "value"),
    [
        pytest.param(StandardText.SORT_BY, "#SORT_BY", id="sort-by"),
        pytest.param(StandardText.SORT_ORDER, "#SORT_ORDER", id="sort-order"),
        pytest.param(StandardText.ASCENDING, "#ASCENDING", id="ascending"),
        pytest.param(StandardText.DESCENDING, "#DESCENDING", id="descending"),
        pytest.param(StandardText.ICON_PLACEHOLDER, "#ICON_PLACEHOLDER", id="icon-placeholder"),
        pytest.param(StandardText.GRAPHICS, "#GRAPHICS", id="graphics"),
        pytest.param(StandardText.AUDIO, "#AUDIO", id="audio"),
        pytest.param(StandardText.GAMEPLAY, "#GAMEPLAY", id="gameplay"),
        pytest.param(StandardText.MISCELLANEOUS, "#MISCELLANEOUS", id="miscellaneous"),
        pytest.param(StandardText.STAGE_COVER, "#STAGE_COVER", id="stage-cover"),
        pytest.param(StandardText.FAVORITE, "#FAVORITE", id="favorite"),
    ],
)
def test_standard_text_1_1_3_and_1_1_4_additions(text, value):
    assert text.value == value


def test_encode_localization_text_passes_plain_text_through():
    assert encode_localization_text("Hello World") == "Hello World"
    assert encode_localization_text(StandardText.MILLISECOND_UNIT) == StandardText.MILLISECOND_UNIT
    assert encode_localization_text(None) is None


def test_encode_localization_text_encodes_dict_compactly():
    assert encode_localization_text(LOCALIZED_GREETING) == ENCODED_GREETING


def test_option_category_localized_title():
    category = OptionCategory(name="greeting", title=LOCALIZED_GREETING)
    assert category.to_dict() == {"name": "greeting", "title": ENCODED_GREETING}


def test_option_str_title_and_description_pass_through():
    option = slider_option(title="Speed", description="Playback speed", default=1.0, min=0.5, max=2.0, step=0.05)
    result = option.to_dict()
    assert result["title"] == "Speed"
    assert result["description"] == "Playback speed"


def test_option_localized_title_and_description():
    localized_description = {"en": "A greeting", "zhs": "问候"}
    encoded_description = '##LOCALIZE:{"en":"A greeting","zhs":"问候"}'
    for option in [
        slider_option(
            title=LOCALIZED_GREETING, description=localized_description, default=1.0, min=0.5, max=2.0, step=0.05
        ),
        toggle_option(title=LOCALIZED_GREETING, description=localized_description, default=True),
        select_option(title=LOCALIZED_GREETING, description=localized_description, default="a", values=["a", "b"]),
    ]:
        result = option.to_dict()
        assert result["title"] == ENCODED_GREETING
        assert result["description"] == encoded_description


def test_option_unset_title_and_description_stay_omitted():
    option = toggle_option(default=False)
    result = option.to_dict()
    assert "title" not in result
    assert "description" not in result


def test_slider_option_localized_unit():
    option = slider_option(default=1.0, min=0.5, max=2.0, step=0.05, unit={"en": "sec", "zhs": "秒"})
    assert option.to_dict()["unit"] == '##LOCALIZE:{"en":"sec","zhs":"秒"}'


def test_select_option_localized_values():
    option = select_option(default="plain", values=["plain", {"en": "Localized", "zhs": "本地化"}])
    result = option.to_dict()
    assert result["def"] == 0
    assert result["values"] == ["plain", '##LOCALIZE:{"en":"Localized","zhs":"本地化"}']


def test_select_option_localized_default_resolves_to_index():
    localized_value = {"en": "Localized", "zhs": "本地化"}
    option = select_option(default=localized_value, values=["plain", localized_value])
    assert option.to_dict()["def"] == 1


def test_select_option_int_default_is_used_as_index_directly():
    option = select_option(default=1, values=["a", "b"])
    assert option.to_dict()["def"] == 1


@pytest.mark.parametrize("default", [-1, 2])
def test_select_option_rejects_out_of_range_int_default(default):
    with pytest.raises(ValueError, match="Select option default index must be between 0 and 1"):
        select_option(default=default, values=["a", "b"])


def test_select_option_rejects_int_default_for_empty_values():
    with pytest.raises(ValueError, match="Select option default index cannot be used with no values"):
        select_option(default=0, values=[])


def test_select_option_rejects_bool_default_as_an_index():
    with pytest.raises(TypeError, match="Select option default index must be an integer, not bool"):
        select_option(default=True, values=["a", "b"])


def test_bucket_localized_unit():
    info = bucket(sprites=[], unit={"en": "ms", "zhs": "毫秒"})
    assert info.to_dict()["unit"] == '##LOCALIZE:{"en":"ms","zhs":"毫秒"}'


def test_instruction_localized_text():
    @instructions
    class _Instructions:
        tap: StandardInstruction.TAP
        custom: Instruction = instruction({"en": "Custom", "zhs": "自定义"})

    assert _Instructions._instructions_ == [
        StandardText.TAP,
        '##LOCALIZE:{"en":"Custom","zhs":"自定义"}',
    ]


def test_item_metadata_keeps_native_localization_dicts():
    # Engine and level item metadata emits native localization dicts in item.json;
    # it must never go through the ##LOCALIZE string encoding.
    engine = Engine(name="test", title=LOCALIZED_GREETING, data=None)
    assert engine.title == LOCALIZED_GREETING
    level = Level(name="test", title=LOCALIZED_GREETING, data=LevelData(bgm_offset=0.0, entities=[]))
    assert level.title == LOCALIZED_GREETING
    assert Tag(title=LOCALIZED_GREETING).as_dict()["title"] == LOCALIZED_GREETING
    assert as_localization_text("Hello World") == {"en": "Hello World"}


@pytest.mark.parametrize("title", ["", {}])
def test_item_metadata_keeps_explicit_empty_titles(title):
    engine = Engine(name="engine", title=title, data=None)
    level = Level(name="level", title=title, data=LevelData(bgm_offset=0.0, entities=[]))

    assert engine.title == as_localization_text(title)
    assert level.title == as_localization_text(title)
