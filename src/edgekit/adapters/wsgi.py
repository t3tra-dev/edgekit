from __future__ import annotations

from typing import TYPE_CHECKING, Generic, Protocol, TypeVar, cast, runtime_checkable

from ..runtime import enter_env_scope, exit_env_scope
from ..webapi import Headers, Request, Response
from ..worker import WorkerEntrypoint

try:
    from werkzeug.test import EnvironBuilder
    from werkzeug.wrappers import Response as WSGIResponse
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError("edgekit.adapters.wsgi requires 'werkzeug' to be installed") from exc

if TYPE_CHECKING:
    from _typeshed.wsgi import WSGIApplication, WSGIEnvironment
else:
    type WSGIApplication = object
    type WSGIEnvironment = dict[str, object]

EnvT = TypeVar("EnvT")


@runtime_checkable
class SupportsWSGIApp(Protocol):
    wsgi_app: WSGIApplication


class WSGI(WorkerEntrypoint[EnvT], Generic[EnvT]):
    app: WSGIApplication | SupportsWSGIApp | None = None

    async def fetch(self, request: Request) -> Response:
        body = await self._read_request_body(request)
        environ = self._build_environ(request, body)
        token = enter_env_scope(cast(object, self.env))
        try:
            wsgi_response = WSGIResponse.from_app(self._resolve_wsgi_application(), environ, buffered=True)
        finally:
            exit_env_scope(token)
        return self._response_from_wsgi(wsgi_response)

    async def _read_request_body(self, request: Request) -> bytes:
        if request.method in {"GET", "HEAD"}:
            return b""
        return await request.bytes()

    def _build_environ(self, request: Request, body: bytes) -> WSGIEnvironment:
        url = request.url
        builder = EnvironBuilder(
            path=url.pathname or "/",
            base_url=url.origin,
            query_string=url.search_params.to_query_string(),
            method=request.method,
            headers=list(request.headers.items()),
            data=body,
        )
        try:
            environ = builder.get_environ()
        finally:
            builder.close()
        environ["edgekit.env"] = self.env
        environ["edgekit.request"] = request
        return environ

    def _resolve_wsgi_application(self) -> WSGIApplication:
        candidate = _declared_application(self, fallback=self.app)
        if candidate is None:
            raise RuntimeError(f"{type(self).__name__}.app must be set to a WSGI application")
        if isinstance(candidate, SupportsWSGIApp):
            return candidate.wsgi_app
        return cast(WSGIApplication, candidate)

    def _response_from_wsgi(self, response: WSGIResponse) -> Response:
        return Response.bytes(
            response.get_data(),
            status=response.status_code,
            headers=Headers(response.headers.to_wsgi_list()),
        )


__all__ = ["WSGI"]


def _declared_application(instance: object, *, fallback: object) -> object:
    return getattr(type(instance), "__dict__", {}).get("app", fallback)
