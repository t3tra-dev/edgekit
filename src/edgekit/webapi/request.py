from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar, cast, overload

from .._js import (
    JSBodyReaderLike,
    JSRequest,
    JSRequestObjectLike,
    JSRequestWrapperLike,
    JSStringValueLike,
    is_js_proxy,
    js_buffer_to_bytes,
    js_object_from_mapping,
    js_to_py,
    require_js_module,
)
from .._utils import MISSING, MissingType, instantiate_type, maybe_await
from ..ffi import to_js, unwrap_raw
from ..webapi.body import BodyValue, body_to_bytes, body_to_text, load_json_body
from ..webapi.headers import Headers
from ..webapi.url import URL

T = TypeVar("T")
_UNSET = object()


class Request:
    def __init__(
        self,
        *,
        method: str,
        url: str | URL,
        headers: Headers | Mapping[str, str] | None = None,
        body: BodyValue | object = _UNSET,
        raw: JSRequest | MissingType = MISSING,
    ) -> None:
        self._method = method
        self._url = url if isinstance(url, URL) else URL(str(url))
        self._headers = headers if isinstance(headers, Headers) else Headers(headers)
        self._body = body
        self._raw = raw

    @classmethod
    def wrap(cls, raw: object) -> "Request":
        if isinstance(raw, Request):
            return raw
        if isinstance(raw, Mapping):
            raw_mapping = cast(Mapping[str, object], raw)
            headers_value = raw_mapping.get("headers")
            return cls(
                method=str(raw_mapping.get("method", "GET")),
                url=str(raw_mapping.get("url", "")),
                headers=cast(Mapping[str, str] | None, headers_value) if isinstance(headers_value, Mapping) else None,
                body=raw_mapping.get("body", _UNSET),
            )
        try:
            raw_proxy = unwrap_raw(raw)
        except TypeError:
            raw_proxy = MISSING
        request_like = raw_proxy if not isinstance(raw_proxy, MissingType) else raw
        if not isinstance(request_like, JSRequestObjectLike):
            raise TypeError(f"Expected Request-like object, got {type(raw).__name__}")

        raw_request = (
            request_like.js_object
            if isinstance(request_like, JSRequestWrapperLike)
            else cast(JSRequest | MissingType, raw_proxy)
        )
        return cls(
            method=_coerce_request_method(request_like.method),
            url=URL.wrap(str(request_like.url)),
            headers=Headers.coerce(request_like.headers),
            body=_UNSET,
            raw=raw_request,
        )

    @property
    def method(self) -> str:
        return self._method

    @property
    def url(self) -> URL:
        return self._url

    @property
    def headers(self) -> Headers:
        return self._headers

    @property
    def raw(self) -> JSRequest:
        if isinstance(self._raw, MissingType):
            self._raw = self._build_js_request()
        return self._raw

    async def text(self) -> str:
        if self._body is not _UNSET:
            return body_to_text(self._body)  # type: ignore[arg-type]
        if not isinstance(self._raw, MissingType):
            return str(await maybe_await(cast(JSBodyReaderLike, self._raw).text()))
        return ""

    async def bytes(self) -> bytes:
        if self._body is not _UNSET:
            return body_to_bytes(self._body)  # type: ignore[arg-type]
        if not isinstance(self._raw, MissingType):
            body_reader = cast(JSBodyReaderLike, self._raw)
            return js_buffer_to_bytes(await maybe_await(body_reader.arrayBuffer()))
        return b""

    @overload
    async def json(self, *, type: None = None) -> object: ...

    @overload
    async def json(self, *, type: type[T]) -> T: ...

    async def json(self, *, type: object | None = None) -> object:
        if self._body is not _UNSET:
            value = load_json_body(self._body)  # type: ignore[arg-type]
        elif not isinstance(self._raw, MissingType):
            value = js_to_py(await maybe_await(cast(JSBodyReaderLike, self._raw).json()))
        else:
            value = load_json_body(await self.text())
        return instantiate_type(type, value)

    def _build_js_request(self) -> JSRequest:
        js_module = require_js_module()
        init: dict[str, object] = {
            "method": self._method,
            "headers": self._headers.raw,
        }
        body = _coerce_body_to_js(self._body)
        if body is not None:
            init["body"] = body
        return js_module.Request.new(self._url.href, js_object_from_mapping(init))


def _coerce_body_to_js(body: BodyValue | object) -> object | None:
    if body is _UNSET or body is None:
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
        return body_to_text(body)  # type: ignore[arg-type]


def _coerce_request_method(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, JSStringValueLike):
        return value.value
    return str(value)
