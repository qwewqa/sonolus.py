from msgspec import Struct, field

from sonolus.server.models.misc import ItemType, Sil

type ServerOption = (
    ServerTextOption |
    ServerTextAreaOption |
    ServerSliderOption |
    ServerToggleOption |
    ServerSelectOption |
    ServerMultiOption |
    ServerServerItemOption |
    ServerServerItemsOption |
    ServerCollectionItemOption |
    ServerFileOption
)

class ServerTextOption(Struct, kw_only=True, omit_defaults=True):
    query: str
    name: str
    description: str | None = None
    required: bool
    type: str
    default: str = field(name="def")
    placeholder: str
    limit: int
    shortcuts: list[str]

class ServerTextAreaOption(Struct, kw_only=True, omit_defaults=True):
    query: str
    name: str
    description: str | None = None
    required: bool
    type: str
    default: str = field(name="def")
    placeholder: str
    limit: int
    shortcuts: list[str]

class ServerSliderOption(Struct, kw_only=True, omit_defaults=True):
    query: str
    name: str
    description: str | None = None
    required: bool
    type: str
    default: int = field(name="def")
    min: int
    max: int
    step: int
    unit: str | None = None

class ServerToggleOption(Struct, kw_only=True, omit_defaults=True):
    query: str
    name: str
    description: str | None = None
    required: bool
    type: str
    default: bool = field(name="def")

class ServerSelectOption(Struct, kw_only=True, omit_defaults=True):
    query: str
    name: str
    description: str | None = None
    required: bool
    type: str
    default: int = field(name="def")
    values: list[str]

class ServerMultiOption(Struct, kw_only=True, omit_defaults=True):
    query: str
    name: str
    description: str | None = None
    required: bool
    type: str
    default: list[bool] = field(name="def")
    values: list[str]

class ServerServerItemOption(Struct, kw_only=True, omit_defaults=True):
    query: str
    name: str
    description: str | None = None
    required: bool
    type: str
    item_type: ItemType
    default: Sil | None = field(name="def")
    allow_other_servers: bool

class ServerServerItemsOption(Struct, kw_only=True, omit_defaults=True):
    query: str
    name: str
    description: str | None = None
    required: bool
    type: str
    item_type: ItemType
    default: list[Sil] = field(name="def")
    allow_other_servers: bool
    limit: int

class ServerCollectionItemOption(Struct, kw_only=True, omit_defaults=True):
    query: str
    name: str
    description: str | None = None
    required: bool
    type: str
    item_type: ItemType

class ServerFileOption(Struct, kw_only=True, omit_defaults=True):
    query: str
    name: str
    description: str | None = None
    required: bool
    type: str


class ServerForm(Struct, kw_only=True, omit_defaults=True):
    type: str
    title: str
    icon: str | None = None
    description: str | None = None
    help: str | None = None
    require_confirmation: bool
    options: list[ServerOption]