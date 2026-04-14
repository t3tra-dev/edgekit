from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..ffi.proxy import JSProxyLike


@runtime_checkable
class SupportsRaw(Protocol):
    @property
    def raw(self) -> JSProxyLike: ...
