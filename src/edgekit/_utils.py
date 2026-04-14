from __future__ import annotations

import inspect
from collections.abc import Awaitable, Mapping
from dataclasses import is_dataclass
from typing import Protocol, TypeVar, cast, get_origin

T = TypeVar("T")


class MissingType:
    __slots__ = ()


MISSING = MissingType()


async def maybe_await(value: Awaitable[T] | T) -> T:
    if inspect.isawaitable(value):
        awaited_value = await cast(Awaitable[T], value)
        return awaited_value
    return cast(T, value)


class SupportsKeywordInit(Protocol):
    def __call__(self, **kwargs: object) -> object: ...


class SupportsSingleArgInit(Protocol):
    def __call__(self, value: object, /) -> object: ...


def instantiate_type(target_type: object | None, value: object) -> object:
    if target_type is None:
        return value

    origin = get_origin(target_type) or target_type

    if origin in {dict, list, tuple, set}:
        return value

    if isinstance(origin, type) and is_dataclass(origin) and isinstance(value, Mapping):
        mapping_value = cast(Mapping[object, object], value)
        if not all(isinstance(key, str) for key in mapping_value):
            return cast(object, value)
        constructor = cast(SupportsKeywordInit, origin)
        kwargs = {key: item for key, item in mapping_value.items() if isinstance(key, str)}
        return constructor(**kwargs)

    if isinstance(origin, type):
        if isinstance(value, origin):
            return value
        constructor = cast(SupportsSingleArgInit, origin)
        try:
            return constructor(value)
        except Exception:
            return value

    return value
