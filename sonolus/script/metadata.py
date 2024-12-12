from typing import Any

type LocalizationText = dict[str, str]
type AnyText = str | LocalizationText


def as_localization_text(text: AnyText) -> LocalizationText:
    if isinstance(text, str):
        return {"en": text}
    return text


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
