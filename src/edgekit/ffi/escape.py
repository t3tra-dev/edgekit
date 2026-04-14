from __future__ import annotations

import pyodide.ffi

from ..typing.protocols import SupportsRaw
from .proxy import JSProxyLike


def unwrap_raw(value: object) -> JSProxyLike:
    if isinstance(value, pyodide.ffi.JsProxy):
        return value
    if isinstance(value, SupportsRaw):
        return value.raw
    raise TypeError(f"Expected a raw-capable value, got {type(value).__name__}")


def unwrap_raw_js_proxy(value: object) -> JSProxyLike:
    return unwrap_raw(value)
