import json
from typing import Any, Literal, overload

type Locale = Literal["el", "en", "es", "fr", "id", "it", "ja", "ko", "pt", "ru", "tr", "vi", "zhs", "zht"]
"""A locale code supported by Sonolus.

These are the codes from [the Sonolus locale list](https://i18n.sonolus.com/list.json): `el` (Greek),
`en` (English), `es` (Spanish), `fr` (French), `id` (Indonesian), `it` (Italian), `ja` (Japanese), `ko` (Korean),
`pt` (Portuguese), `ru` (Russian), `tr` (Turkish), `vi` (Vietnamese), `zhs` (Simplified Chinese), and
`zht` (Traditional Chinese).
"""

type LocalizationText = dict[Locale | str, str]
"""Text localized per locale."""

type AnyText = str | LocalizationText
"""Text given either as a plain string or as a localization dict."""

_LOCALIZE_PREFIX = "##LOCALIZE:"


def as_localization_text(text: AnyText) -> LocalizationText:
    """Convert text into a localization dict, treating a plain string as `en`."""
    if isinstance(text, str):
        return {"en": text}
    return text


@overload
def encode_localization_text(text: AnyText) -> str: ...


@overload
def encode_localization_text(text: None) -> None: ...


@overload
def encode_localization_text(text: AnyText | None) -> str | None: ...


def encode_localization_text(text: AnyText | None) -> str | None:
    """Encode text into a single string for fields that only accept strings.

    A localization dict maps [locale codes][sonolus.script.metadata.Locale] to text, and is encoded as
    `##LOCALIZE:` followed by compact JSON, e.g. `##LOCALIZE:{"en":"Hello World","zhs":"你好世界"}`, for supporting
    servers to expand. Plain strings and `None` pass through unchanged.

    Item metadata such as [`Engine`][sonolus.script.engine.Engine] and [`Level`][sonolus.script.level.Level] titles
    supports localization dicts natively and does not use this encoding.
    """
    if not isinstance(text, dict):
        return text
    return f"{_LOCALIZE_PREFIX}{json.dumps(text, ensure_ascii=False, separators=(',', ':'))}"


class Tag:
    """A tag for an engine or level.

    Args:
        title: The title of the tag.
        icon: The icon of the tag.
    """

    title: LocalizationText
    icon: str | None

    def __init__(self, title: AnyText, icon: str | None = None) -> None:
        self.title = as_localization_text(title)
        self.icon = icon

    def as_dict(self) -> dict[str, Any]:
        result = {"title": self.title}
        if self.icon is not None:
            result["icon"] = self.icon
        return result
