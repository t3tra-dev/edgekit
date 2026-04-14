from __future__ import annotations

from edgekit._js import JSDurableObjectId, JSDurableObjectNamespaceLike, JSDurableObjectStubLike, JSRequest

from .._utils import maybe_await
from ..webapi.request import Request
from ..webapi.response import Response


class DurableObjectStub:
    def __init__(self, raw: JSDurableObjectStubLike) -> None:
        self._raw = raw

    @property
    def raw(self) -> JSDurableObjectStubLike:
        return self._raw

    async def fetch(self, request: Request | JSRequest | str) -> Response:
        raw_request = request.raw if isinstance(request, Request) else request
        raw_response = await maybe_await(self._raw.fetch(raw_request))
        return Response.wrap(raw_response)


class DurableObjectNamespace:
    _binding_kind = "durable_object"

    def __init__(self, raw: JSDurableObjectNamespaceLike) -> None:
        self._raw = raw

    @classmethod
    def wrap(cls, raw: JSDurableObjectNamespaceLike) -> DurableObjectNamespace:
        if isinstance(raw, DurableObjectNamespace):
            return raw
        return cls(raw)

    @property
    def raw(self) -> JSDurableObjectNamespaceLike:
        return self._raw

    @property
    def binding_kind(self) -> str:
        return self._binding_kind

    def id_from_name(self, name: str) -> JSDurableObjectId:
        return self._raw.idFromName(name)

    def get(self, object_id: JSDurableObjectId) -> DurableObjectStub:
        return DurableObjectStub(self._raw.get(object_id))
