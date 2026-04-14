import pyodide.ffi

from .proxy import JSProxyLike


def to_js(value: object) -> JSProxyLike:
    return pyodide.ffi.to_js(value)
