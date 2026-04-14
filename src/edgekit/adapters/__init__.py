from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .asgi import ASGI
    from .wsgi import WSGI


def __getattr__(name: str) -> Any:
    if name == "ASGI":
        from .asgi import ASGI

        return ASGI
    if name == "WSGI":
        from .wsgi import WSGI

        return WSGI
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ASGI", "WSGI"]
