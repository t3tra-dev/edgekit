from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, MutableMapping
from typing import cast

from .._js import JSHeaderItemsLike, JSHeaders, JSIterableEntries, is_js_instance, js_headers_from_items
from .._utils import MISSING, MissingType


class Headers(MutableMapping[str, str]):
    def __init__(
        self,
        values: "Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None" = None,
        *,
        raw: JSHeaders | MissingType = MISSING,
    ) -> None:
        self._store: dict[str, tuple[str, str]] = {}
        self._raw = raw

        pairs: Iterable[tuple[str, str]] = ()
        if values is None:
            return
        if isinstance(values, Headers):
            pairs = values.items()
        elif isinstance(values, Mapping):
            mapping_values = cast(Mapping[str, str], values)
            pairs = mapping_values.items()
        else:
            pairs = values

        for key, value in pairs:
            self._store[key.lower()] = (str(key), str(value))

    @classmethod
    def wrap(cls, raw: "Headers | Mapping[str, str] | JSHeaders") -> "Headers":
        if isinstance(raw, Headers):
            return raw
        if isinstance(raw, Mapping):
            return cls(raw)
        if not is_js_instance(raw, "Headers"):
            raise TypeError(f"Expected js.Headers, got {type(raw).__name__}")

        headers = cls(raw=raw)
        entries = cast(JSIterableEntries, raw).entries()
        for key, value in entries:
            headers._store[str(key).lower()] = (str(key), str(value))
        return headers

    @classmethod
    def coerce(cls, raw: object | MissingType = MISSING) -> "Headers":
        if raw is None or isinstance(raw, MissingType):
            return cls()
        if isinstance(raw, Headers):
            return raw
        if isinstance(raw, Mapping):
            return cls(cast(Mapping[str, str], raw))
        if isinstance(raw, JSHeaderItemsLike):
            return cls((str(key), str(value)) for key, value in raw.items())
        if is_js_instance(raw, "Headers"):
            return cls.wrap(cast(JSHeaders, raw))
        raise TypeError(f"Expected Headers-like object, got {type(raw).__name__}")

    @property
    def raw(self) -> JSHeaders:
        if isinstance(self._raw, MissingType):
            self._raw = js_headers_from_items(self.items())
        return self._raw

    def __getitem__(self, key: str) -> str:
        return self._store[key.lower()][1]

    def __setitem__(self, key: str, value: str) -> None:
        self._store[key.lower()] = (key, value)
        self._raw = MISSING

    def __delitem__(self, key: str) -> None:
        del self._store[key.lower()]
        self._raw = MISSING

    def __iter__(self) -> Iterator[str]:
        for original, _ in self._store.values():
            yield original

    def __len__(self) -> int:
        return len(self._store)

    def append(self, key: str, value: str) -> None:
        self[key] = value

    def to_dict(self) -> dict[str, str]:
        return {original: value for original, value in self._store.values()}

    def copy(self) -> "Headers":
        return Headers(self.items())
