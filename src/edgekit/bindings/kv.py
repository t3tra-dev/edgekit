from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from .._js import JSKVListData, JSKVNamespaceLike, js_to_py
from .._utils import maybe_await

T = TypeVar("T")


@dataclass(slots=True, frozen=True)
class KVKey:
    name: str


@dataclass(slots=True)
class KVListResult:
    keys: list[KVKey]
    list_complete: bool = True
    cursor: str | None = None


class KVNamespace(Generic[T]):
    _binding_kind = "kv"

    def __init__(self, raw: JSKVNamespaceLike | MutableMapping[str, T]) -> None:
        self._raw = raw

    @classmethod
    def wrap(cls, raw: JSKVNamespaceLike | MutableMapping[str, T]) -> KVNamespace[T]:
        if isinstance(raw, KVNamespace):
            return raw
        return cls(raw)

    @property
    def raw(self) -> JSKVNamespaceLike | MutableMapping[str, T]:
        return self._raw

    @property
    def binding_kind(self) -> str:
        return self._binding_kind

    async def get(self, key: str) -> T | None:
        if isinstance(self._raw, MutableMapping):
            return self._raw.get(key)
        value = await maybe_await(self._raw.get(key))
        return cast(T | None, js_to_py(value))

    async def get_text(self, key: str) -> str | None:
        value = await self.get(key)
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    async def get_bytes(self, key: str) -> bytes | None:
        value = await self.get(key)
        if value is None:
            return None
        if isinstance(value, bytes):
            return value
        return str(value).encode("utf-8")

    async def put(self, key: str, value: T, *, ttl: int | None = None) -> None:
        if isinstance(self._raw, MutableMapping):
            self._raw[key] = value
            return
        if ttl is None:
            await maybe_await(self._raw.put(key, value))
            return
        try:
            await maybe_await(self._raw.put(key, value, expiration_ttl=ttl))
        except TypeError:
            await maybe_await(self._raw.put(key, value, ttl=ttl))

    async def delete(self, key: str) -> None:
        if isinstance(self._raw, MutableMapping):
            self._raw.pop(key, None)
            return
        await maybe_await(self._raw.delete(key))

    async def list(self, *, prefix: str | None = None, limit: int | None = None) -> KVListResult:
        if isinstance(self._raw, MutableMapping):
            names = [name for name in self._raw if prefix is None or name.startswith(prefix)]
            if limit is not None:
                names = names[:limit]
            return KVListResult(keys=[KVKey(name=name) for name in names])
        result = cast(JSKVListData, js_to_py(await maybe_await(self._raw.list(prefix=prefix, limit=limit))))
        keys = [KVKey(name=item["name"]) for item in result.get("keys", ())]
        return KVListResult(
            keys=keys,
            list_complete=bool(result.get("list_complete", True)),
            cursor=result.get("cursor"),
        )
