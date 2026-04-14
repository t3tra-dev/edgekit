from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar, cast, overload

from .._js import (
    JSBodyReaderLike,
    JSResponse,
    JSResponseObjectLike,
    is_js_instance,
    is_js_proxy,
    js_buffer_to_bytes,
    js_object_from_mapping,
    js_to_py,
    require_js_module,
)
from .._utils import MISSING, MissingType, instantiate_type, maybe_await
from ..ffi import to_js, unwrap_raw
from ..typing.json import JSONValue
from ..webapi.body import BodyValue, body_to_bytes, body_to_text, load_json_body
from ..webapi.headers import Headers

T = TypeVar("T")


class Response:
    def __init__(
        self,
        body: BodyValue = None,
        *,
        status: int = 200,
        headers: Headers | Mapping[str, str] | None = None,
        raw: JSResponse | MissingType = MISSING,
    ) -> None:
        self._body = body
        self._status = status
        self._headers = headers if isinstance(headers, Headers) else Headers(headers)
        self._raw = raw

    @classmethod
    def wrap(cls, raw: object) -> "Response":
        if isinstance(raw, Response):
            return raw
        try:
            raw_proxy = unwrap_raw(raw)
        except TypeError:
            raw_proxy = MISSING
        response_like = raw_proxy if not isinstance(raw_proxy, MissingType) else raw
        if not isinstance(response_like, JSResponseObjectLike) and not is_js_instance(response_like, "Response"):
            raise TypeError(f"Expected Response-like object, got {type(raw).__name__}")
        return cls(
            status=int(cast(JSResponseObjectLike, response_like).status),
            headers=Headers.coerce(cast(JSResponseObjectLike, response_like).headers),
            raw=cast(JSResponse | MissingType, raw_proxy),
        )

    @classmethod
    def text(
        cls,
        body: str,
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> "Response":
        merged_headers = Headers(headers)
        merged_headers.setdefault("content-type", "text/plain; charset=utf-8")
        return cls(body, status=status, headers=merged_headers)

    @classmethod
    def json(
        cls,
        body: JSONValue,
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> "Response":
        merged_headers = Headers(headers)
        merged_headers.setdefault("content-type", "application/json; charset=utf-8")
        return cls(body_to_text(body), status=status, headers=merged_headers)

    @classmethod
    def bytes(
        cls,
        body: bytes,
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> "Response":
        return cls(body, status=status, headers=headers)

    @property
    def status(self) -> int:
        return self._status

    @property
    def headers(self) -> Headers:
        return self._headers

    @property
    def raw(self) -> JSResponse:
        if isinstance(self._raw, MissingType):
            self._raw = self._build_js_response()
        return self._raw

    async def read_text(self) -> str:
        if self._body is not None:
            return body_to_text(self._body)
        if not isinstance(self._raw, MissingType):
            return str(await maybe_await(cast(JSBodyReaderLike, self._raw).text()))
        return ""

    async def read_bytes(self) -> bytes:
        if self._body is not None:
            return body_to_bytes(self._body)
        if not isinstance(self._raw, MissingType):
            body_reader = cast(JSBodyReaderLike, self._raw)
            return js_buffer_to_bytes(await maybe_await(body_reader.arrayBuffer()))
        return b""

    @overload
    async def read_json(self, *, type: None = None) -> object: ...

    @overload
    async def read_json(self, *, type: type[T]) -> T: ...

    async def read_json(self, *, type: object | None = None) -> object:
        if self._body is not None:
            value = load_json_body(self._body)
        elif not isinstance(self._raw, MissingType):
            value = js_to_py(await maybe_await(cast(JSBodyReaderLike, self._raw).json()))
        else:
            value = load_json_body(await self.read_text())
        return instantiate_type(type, value)

    def _build_js_response(self) -> JSResponse:
        js_module = require_js_module()
        init = js_object_from_mapping({"status": self._status, "headers": self._headers.raw})
        body = _coerce_body_to_js(self._body)
        return js_module.Response.new(body, init)


def _coerce_body_to_js(body: BodyValue) -> object | None:
    if body is None:
        return None
    if isinstance(body, str):
        return body
    if isinstance(body, (bytes, bytearray)):
        return to_js(bytes(body))
    if is_js_proxy(body):
        return body
    try:
        return unwrap_raw(body)
    except TypeError:
        return body_to_text(body)
