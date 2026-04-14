from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import Protocol, TypeVar, cast

from ..runtime import enter_env_scope, exit_env_scope
from ..webapi import Request, Response
from ..worker import WorkerEntrypoint

EnvT = TypeVar("EnvT")

type ASGIMessage = Mapping[str, object]
type ASGIScope = MutableMapping[str, object]


class ASGIReceive(Protocol):
    async def __call__(self) -> ASGIMessage: ...


class ASGISend(Protocol):
    async def __call__(self, message: ASGIMessage) -> None: ...


class ASGIApplication(Protocol):
    async def __call__(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> object: ...


class ASGI(WorkerEntrypoint[EnvT]):
    app: ASGIApplication | None = None

    async def fetch(self, request: Request) -> Response:
        asgi_module = _require_asgi_module()
        wrapped_app = _EnvScopedASGIApplication(self._resolve_asgi_application(), self.env)
        raw_request = request.raw
        if _is_websocket_request(request):
            return Response.wrap(await asgi_module.websocket(wrapped_app, raw_request))
        return Response.wrap(await asgi_module.fetch(wrapped_app, raw_request, self.env, self.ctx))

    def _resolve_asgi_application(self) -> ASGIApplication:
        candidate = _declared_application(self, fallback=self.app)
        if candidate is None:
            raise RuntimeError(f"{type(self).__name__}.app must be set to an ASGI application")
        return cast(ASGIApplication, candidate)


class _EnvScopedASGIApplication(ASGIApplication):
    def __init__(self, app: ASGIApplication, env: object) -> None:
        self._app = app
        self._env = env

    async def __call__(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> object:
        scope["env"] = self._env
        token = enter_env_scope(self._env)
        try:
            return await self._app(scope, receive, send)
        finally:
            exit_env_scope(token)


class _ASGIModule(Protocol):
    fetch: Callable[[ASGIApplication, object, object, object], Awaitable[object]]
    websocket: Callable[[ASGIApplication, object], Awaitable[object]]


def _require_asgi_module() -> _ASGIModule:
    try:
        import asgi as asgi_module
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError("edgekit.adapters.asgi requires the 'asgi' runtime module") from exc
    return cast(_ASGIModule, asgi_module)


def _is_websocket_request(request: Request) -> bool:
    return request.headers.get("upgrade", "").lower() == "websocket"


def _declared_application(instance: object, *, fallback: object) -> object:
    return getattr(type(instance), "__dict__", {}).get("app", fallback)


__all__ = ["ASGI", "ASGIApplication", "ASGIMessage", "ASGIReceive", "ASGIScope", "ASGISend"]
