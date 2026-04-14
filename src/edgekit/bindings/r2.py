from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field

from .._js import JSR2BucketLike, JSR2HTTPMetadataLike, JSR2ObjectLike, js_buffer_to_bytes, js_to_py
from .._utils import maybe_await
from ..core.errors import BindingError


@dataclass(slots=True, frozen=True)
class _StaticR2HTTPMetadata:
    contentType: str | None = None


@dataclass(slots=True)
class _StaticR2Object:
    key: str
    body: bytes
    httpMetadata: _StaticR2HTTPMetadata
    size: int = field(init=False)

    def __post_init__(self) -> None:
        self.size = len(self.body)

    async def arrayBuffer(self) -> bytes:
        return self.body

    async def text(self) -> str:
        return self.body.decode("utf-8")

    async def json(self) -> object:
        return json.loads(await self.text())


@dataclass(slots=True)
class R2Object:
    key: str
    raw: JSR2ObjectLike
    body: bytes | None = None
    content_type: str | None = None

    @property
    def size(self) -> int:
        if self.body is not None:
            return len(self.body)
        return int(self.raw.size)

    async def bytes(self) -> bytes:
        if self.body is not None:
            return self.body
        self.body = js_buffer_to_bytes(await maybe_await(self.raw.arrayBuffer()))
        return self.body

    async def text(self) -> str:
        if self.body is not None:
            return self.body.decode("utf-8")
        if isinstance(self.raw, _StaticR2Object):
            return await self.raw.text()
        return str(await maybe_await(self.raw.text()))

    async def json(self) -> object:
        if self.body is not None:
            return json.loads(self.body.decode("utf-8"))
        if isinstance(self.raw, _StaticR2Object):
            return await self.raw.json()
        return js_to_py(await maybe_await(self.raw.json()))


class R2Bucket:
    _binding_kind = "r2"

    def __init__(self, raw: JSR2BucketLike | MutableMapping[str, R2Object]) -> None:
        self._raw = raw

    @classmethod
    def wrap(cls, raw: JSR2BucketLike | MutableMapping[str, R2Object]) -> "R2Bucket":
        if isinstance(raw, R2Bucket):
            return raw
        return cls(raw)

    @property
    def raw(self) -> JSR2BucketLike | MutableMapping[str, R2Object]:
        return self._raw

    @property
    def binding_kind(self) -> str:
        return self._binding_kind

    async def get(self, key: str) -> R2Object | None:
        if isinstance(self._raw, Mapping):
            value = self._raw.get(key)
            if value is None:
                return None
            return _coerce_r2_object(key, value)
        result = await maybe_await(self._raw.get(key))
        if result is None:
            return None
        return _coerce_r2_object(key, result)

    async def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> R2Object:
        if isinstance(self._raw, MutableMapping):
            obj = _coerce_r2_object(key, data, content_type=content_type)
            self._raw[key] = obj
            return obj
        if content_type is None:
            result = await maybe_await(self._raw.put(key, data))
        else:
            try:
                result = await maybe_await(self._raw.put(key, data, http_metadata={"contentType": content_type}))
            except TypeError:
                result = await maybe_await(self._raw.put(key, data, content_type=content_type))
        return _coerce_r2_object(key, result if result is not None else data, content_type=content_type)

    async def put_text(
        self,
        key: str,
        data: str,
        *,
        content_type: str = "text/plain; charset=utf-8",
    ) -> R2Object:
        return await self.put_bytes(key, data.encode("utf-8"), content_type=content_type)

    async def put_json(self, key: str, value: object) -> R2Object:
        return await self.put_text(key, json.dumps(value, ensure_ascii=False), content_type="application/json")

    async def delete(self, key: str) -> None:
        if isinstance(self._raw, MutableMapping):
            self._raw.pop(key, None)
            return
        await maybe_await(self._raw.delete(key))


def _coerce_r2_object(key: str, value: object, *, content_type: str | None = None) -> R2Object:
    if isinstance(value, R2Object):
        return value
    if isinstance(value, bytes):
        raw = _StaticR2Object(key=key, body=value, httpMetadata=_StaticR2HTTPMetadata(content_type))
        return R2Object(key=key, raw=raw, body=value, content_type=content_type)
    if isinstance(value, str):
        body = value.encode("utf-8")
        raw = _StaticR2Object(key=key, body=body, httpMetadata=_StaticR2HTTPMetadata(content_type))
        return R2Object(key=key, raw=raw, body=body, content_type=content_type)
    if isinstance(value, JSR2ObjectLike):
        metadata = value.httpMetadata
        metadata_content_type = metadata.contentType if isinstance(metadata, JSR2HTTPMetadataLike) else None
        return R2Object(
            key=str(value.key),
            raw=value,
            body=None,
            content_type=metadata_content_type or content_type,
        )
    raise BindingError(f"Unsupported R2 object type: {type(value)!r}")
