from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from typing import cast
from urllib.parse import parse_qs, urlencode, urlparse

from .._js import (
    JSURL,
    JSHrefLike,
    JSImportURLLike,
    JSIterableEntries,
    JSURLObjectLike,
    JSURLSearchParams,
    is_js_instance,
    js_url_from_string,
    js_url_search_params_from_pairs,
)
from .._utils import MISSING, MissingType


class URLSearchParams(MutableMapping[str, str]):
    def __init__(self, query: str = "", *, raw: JSURLSearchParams | MissingType = MISSING) -> None:
        self._values = parse_qs(query.lstrip("?"), keep_blank_values=True)
        self._raw = raw

    @classmethod
    def wrap(cls, raw: "URLSearchParams | JSURLSearchParams | str") -> "URLSearchParams":
        if isinstance(raw, URLSearchParams):
            return raw
        if isinstance(raw, str):
            return cls(raw)
        if not is_js_instance(raw, "URLSearchParams"):
            raise TypeError(f"Expected js.URLSearchParams, got {type(raw).__name__}")

        params = cls(raw=raw)
        for key, value in cast(JSIterableEntries, raw).entries():
            params.add(str(key), str(value))
        return params

    @property
    def raw(self) -> JSURLSearchParams:
        if isinstance(self._raw, MissingType):
            self._raw = js_url_search_params_from_pairs(
                (key, value) for key, values in self._values.items() for value in values
            )
        return self._raw

    def __getitem__(self, key: str) -> str:
        return self._values[key][-1]

    def __setitem__(self, key: str, value: str) -> None:
        self._values[key] = [value]
        self._raw = MISSING

    def __delitem__(self, key: str) -> None:
        del self._values[key]
        self._raw = MISSING

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def add(self, key: str, value: str) -> None:
        self._values.setdefault(key, []).append(value)
        self._raw = MISSING

    def get_all(self, key: str) -> list[str]:
        return list(self._values.get(key, ()))

    def to_query_string(self) -> str:
        return urlencode(self._values, doseq=True)


class URL:
    def __init__(self, value: str, *, raw: JSURL | MissingType = MISSING) -> None:
        self._value = value
        self._parsed = urlparse(value)
        self._raw = raw
        self._search_params = URLSearchParams(self._parsed.query)
        if not isinstance(raw, MissingType):
            self._search_params = URLSearchParams.wrap(cast(JSURLObjectLike, raw).searchParams)

    @classmethod
    def wrap(cls, raw: "URL | JSURL | str | object") -> "URL":
        if isinstance(raw, URL):
            return raw
        if isinstance(raw, str):
            return cls(raw)
        if is_js_instance(raw, "URL"):
            js_url = cast(JSURL, raw)
            return cls(str(js_url.href), raw=js_url)
        if isinstance(raw, JSHrefLike):
            return cls(str(raw.href))
        if isinstance(raw, JSImportURLLike):
            return cls(str(raw.url))
        return cls(str(raw))

    @property
    def raw(self) -> JSURL:
        if isinstance(self._raw, MissingType):
            self._raw = js_url_from_string(self._value)
            self._search_params = URLSearchParams.wrap(self._raw.searchParams)
        return self._raw

    @property
    def href(self) -> str:
        return self._value

    @property
    def pathname(self) -> str:
        return self._parsed.path

    @property
    def search(self) -> str:
        return f"?{self._parsed.query}" if self._parsed.query else ""

    @property
    def search_params(self) -> URLSearchParams:
        return self._search_params

    @property
    def origin(self) -> str:
        if not self._parsed.scheme or not self._parsed.netloc:
            return ""
        return f"{self._parsed.scheme}://{self._parsed.netloc}"

    @property
    def hostname(self) -> str:
        return self._parsed.hostname or ""

    def __str__(self) -> str:
        return self._value
