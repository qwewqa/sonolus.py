import gzip

from litestar import get, Response, MediaType
from litestar.exceptions import NotFoundException
from litestar.params import Dependency

import hashlib
from functools import cached_property, lru_cache
from os import PathLike
from pathlib import Path
from struct import Struct
from typing import Protocol, Annotated


class Repository:
    values_by_hash: dict[str, bytes]

    def __init__(self):
        values = {}



#
#
# class ResourceProvider(Protocol):
#     def get_resource(self, path: str) -> Resource | None:
#         ...
#
#
# class FileResourceProvider:
#     def __init__(self, base: PathLike):
#         self.base = Path(base)
#
#     @lru_cache
#     def get_resource(self, path: str) -> Resource | None:
#         full = self.base / path
#         if not full.is_file():
#             return None
#         data = full.read_bytes()
#         if full.suffix == ".json":
#             data = gzip.compress(data)
#         return Resource(data)
#
#
# class CompositeResourceProvider:
#     def __init__(self, providers: list[ResourceProvider]):
#         self.providers = providers
#
#     def get_resource(self, path: str) -> Resource | None:
#         for provider in self.providers:
#             resource = provider.get_resource(path)
#             if resource is not None:
#                 return resource
#         return None
#
#
# @get("/resource/{path:str}", media_type="application/octet-stream")
# def get_resource(path: str, provider: Annotated[ResourceProvider, Dependency()]):
#     resource = provider.get_resource(path)
#     if resource is None:
#         raise NotFoundException("Resource not found")
#     return resource.data
